#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from core.path_resolver import resolve_remote_path
from core.sftp_service import listdir
from core.ssh_session import SSHSession
from log_util import logger
from models.session_item import SessionItem
from storage.keyring_store import KeyringStore
from ui.terminal_vt_widget import TerminalVTWidget


class ConnectionManager(QObject):
    """Maps terminal tab IDs to live SSH sessions."""

    remote_list_updated = pyqtSignal(str)

    def __init__(
        self,
        keyring_store: Optional[KeyringStore] = None,
        parent: QObject = None,
    ) -> None:
        super().__init__(parent)
        self.keyring = keyring_store or KeyringStore()
        self._sessions: Dict[str, SSHSession] = {}
        self._terminals: Dict[str, TerminalVTWidget] = {}
        self._tab_titles: Dict[str, str] = {}
        self._remote_cache: Dict[str, Dict[str, List[dict]]] = {}

    def get_session(self, tab_id: str) -> Optional[SSHSession]:
        return self._sessions.get(tab_id)

    def get_tab_title(self, tab_id: str) -> str:
        return self._tab_titles.get(tab_id, '')

    def get_remote_list_callback(self, tab_id: str) -> Optional[Callable[[str], List[dict]]]:
        if tab_id not in self._sessions:
            return None

        def _callback(path: str) -> List[dict]:
            cache = self._remote_cache.setdefault(tab_id, {})
            if path in cache:
                return cache[path]
            asyncio.create_task(self.refresh_remote_list(tab_id, path))
            return cache.get(path, [])

        return _callback

    def invalidate_remote_cache(self, tab_id: str, path: Optional[str] = None) -> None:
        cache = self._remote_cache.get(tab_id)
        if cache is None:
            return
        if path is None:
            cache.clear()
        else:
            cache.pop(path, None)

    async def refresh_remote_list(self, tab_id: str, path: str) -> List[dict]:
        ssh = self._sessions.get(tab_id)
        if ssh is None:
            return []
        sftp = ssh.get_sftp()
        if sftp is None:
            return []
        try:
            entries = await listdir(sftp, path)
        except Exception as exc:
            logger.warning(f'Remote list failed: {exc}')
            entries = []
        self._remote_cache.setdefault(tab_id, {})[path] = entries
        self.remote_list_updated.emit(tab_id)
        return entries

    async def resolve_remote_path(self, tab_id: str, configured: str) -> str:
        ssh = self._sessions.get(tab_id)
        if ssh is None:
            return '/'
        sftp = ssh.get_sftp()
        if sftp is None:
            return '/'
        username = ''
        session_item = ssh.session_item
        if session_item is not None:
            username = session_item.username
        return await resolve_remote_path(sftp, configured, username=username)

    async def open_tab(
        self,
        tab_id: str,
        session_item: SessionItem,
        terminal: TerminalVTWidget,
        *,
        on_connected: Optional[Callable[[], None]] = None,
        on_disconnected: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        if tab_id in self._sessions:
            await self.close_tab(tab_id)

        password = self.keyring.get_password(session_item.id)
        cols, rows = terminal._calc_cols_rows()

        ssh = SSHSession(self)
        self._sessions[tab_id] = ssh
        self._terminals[tab_id] = terminal
        self._tab_titles[tab_id] = session_item.name
        self._remote_cache[tab_id] = {}

        def _on_data(text: str) -> None:
            terminal.write_text(text)

        def _on_disconnected() -> None:
            if on_disconnected is not None:
                on_disconnected()

        def _on_error(message: str) -> None:
            terminal.write_text(f'\r\n{message}\r\n')
            if on_error is not None:
                on_error(message)

        ssh.data_received.connect(_on_data)
        ssh.disconnected.connect(_on_disconnected)
        ssh.error.connect(_on_error)
        terminal.input_received.connect(ssh.write)

        try:
            await ssh.connect(session_item, password=password, cols=cols, rows=rows)
        except Exception:
            self._sessions.pop(tab_id, None)
            self._terminals.pop(tab_id, None)
            self._tab_titles.pop(tab_id, None)
            self._remote_cache.pop(tab_id, None)
            raise

        if on_connected is not None:
            on_connected()

    async def resize_terminal(self, tab_id: str) -> None:
        ssh = self._sessions.get(tab_id)
        terminal = self._terminals.get(tab_id)
        if ssh is None or terminal is None:
            return
        cols, rows = terminal._calc_cols_rows()
        await ssh.resize(cols, rows)

    async def close_tab(self, tab_id: str) -> None:
        ssh = self._sessions.pop(tab_id, None)
        self._terminals.pop(tab_id, None)
        self._tab_titles.pop(tab_id, None)
        self._remote_cache.pop(tab_id, None)
        if ssh is not None:
            await ssh.disconnect()

    async def close_all(self) -> None:
        for tab_id in list(self._sessions.keys()):
            await self.close_tab(tab_id)
