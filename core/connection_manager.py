#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import shlex
from typing import Callable

from PyQt5.QtCore import QObject, pyqtSignal

from core.path_resolver import resolve_remote_path
from core.sftp_service import listdir
from core.ssh_session import HostKeyConfirm, SSHSession
from core.terminal_port import TerminalPort
from log_util import logger
from models.session_item import SessionItem
from storage.credential_store import CredentialStore
from storage.host_key_store import HostKeyStore


class ConnectionManager(QObject):
    """Maps terminal tab IDs to live SSH sessions."""

    remote_list_updated = pyqtSignal(str)

    def __init__(
        self,
        credential_store: CredentialStore | None = None,
        parent: QObject = None,
    ) -> None:
        super().__init__(parent)
        self.keyring = credential_store or CredentialStore()
        self.host_keys = HostKeyStore()
        self._sessions: dict[str, SSHSession] = {}
        self._terminals: dict[str, TerminalPort] = {}
        self._tab_titles: dict[str, str] = {}
        self._remote_cache: dict[str, dict[str, list[dict]]] = {}
        self._refresh_tasks: dict[tuple[str, str], asyncio.Task] = {}

    def get_session(self, tab_id: str) -> SSHSession | None:
        return self._sessions.get(tab_id)

    def get_tab_title(self, tab_id: str) -> str:
        return self._tab_titles.get(tab_id, '')

    def get_remote_list_callback(self, tab_id: str) -> Callable[[str], list[dict]] | None:
        if tab_id not in self._sessions:
            return None

        def _callback(path: str) -> list[dict]:
            cache = self._remote_cache.setdefault(tab_id, {})
            if path in cache:
                return cache[path]
            key = (tab_id, path)
            existing = self._refresh_tasks.get(key)
            if existing is None or existing.done():
                task = asyncio.create_task(self.refresh_remote_list(tab_id, path))
                self._refresh_tasks[key] = task
                task.add_done_callback(lambda done, k=key: self._refresh_tasks.pop(k, None))
            return cache.get(path, [])

        return _callback

    def invalidate_remote_cache(self, tab_id: str, path: str | None = None) -> None:
        cache = self._remote_cache.get(tab_id)
        if cache is None:
            return
        if path is None:
            cache.clear()
        else:
            cache.pop(path, None)

    async def refresh_remote_list(self, tab_id: str, path: str) -> list[dict]:
        ssh = self._sessions.get(tab_id)
        if ssh is None:
            return []
        sftp = ssh.get_sftp()
        if sftp is None:
            return []
        cache = self._remote_cache.setdefault(tab_id, {})
        try:
            entries = await listdir(sftp, path, tab_id=tab_id)
        except Exception as exc:
            logger.warning(f'Remote list failed: tab_id={tab_id}, path={path}, error={exc}')
            return cache.get(path, [])
        cache[path] = entries
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

    async def cd_shell(self, tab_id: str, remote_path: str) -> None:
        """Send ``cd`` into the interactive shell for the given tab."""
        path = (remote_path or '').strip()
        if not path or tab_id not in self._sessions:
            return
        # Brief delay so login banner / first prompt can settle.
        await asyncio.sleep(0.15)
        ssh = self._sessions.get(tab_id)
        if ssh is None:
            return
        # write() no-ops when the shell process is already gone
        ssh.write(f'cd {shlex.quote(path)}\r'.encode('utf-8'))

    async def open_tab(
        self,
        tab_id: str,
        session_item: SessionItem,
        terminal: TerminalPort,
        *,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        host_key_confirm: HostKeyConfirm | None = None,
    ) -> None:
        if tab_id in self._sessions:
            logger.info(f'Connection tab already exists, closing old tab first: tab_id={tab_id}')
            await self.close_tab(tab_id)

        password = self.keyring.get_password(session_item.id)
        cols, rows = terminal.terminal_size()
        logger.info(
            'Open connection tab: '
            f'tab_id={tab_id}, session_id={session_item.id}, name={session_item.name}, '
            f'host={session_item.host}, port={session_item.port}, username={session_item.username}'
        )

        ssh = SSHSession(tab_id, self, self.host_keys)
        self._sessions[tab_id] = ssh
        self._terminals[tab_id] = terminal
        self._tab_titles[tab_id] = session_item.name
        self._remote_cache[tab_id] = {}

        def _on_data(text: str) -> None:
            if tab_id not in self._terminals:
                return
            term = self._terminals.get(tab_id)
            if term is None:
                return
            try:
                term.write_text(text)
            except RuntimeError:
                return

        def _on_disconnected() -> None:
            self._discard_disconnected_session(tab_id, ssh, terminal)
            if on_disconnected is not None:
                on_disconnected()

        def _on_error(message: str) -> None:
            if tab_id not in self._terminals:
                return
            term = self._terminals.get(tab_id)
            if term is None:
                return
            try:
                term.write_text(f'\r\n{message}\r\n')
            except RuntimeError:
                return
            if on_error is not None:
                on_error(message)

        ssh.data_received.connect(_on_data)
        ssh.disconnected.connect(_on_disconnected)
        ssh.error.connect(_on_error)
        terminal.input_received.connect(ssh.write)

        try:
            await ssh.connect(
                session_item,
                password=password,
                cols=cols,
                rows=rows,
                host_key_confirm=host_key_confirm,
            )
        except asyncio.CancelledError:
            logger.info(
                'Open connection tab cancelled: '
                f'tab_id={tab_id}, session_id={session_item.id}, host={session_item.host}'
            )
            self._discard_failed_session(tab_id, ssh, terminal)
            raise
        except Exception:
            logger.warning(
                'Open connection tab failed: '
                f'tab_id={tab_id}, session_id={session_item.id}, host={session_item.host}'
            )
            self._discard_failed_session(tab_id, ssh, terminal)
            raise

        # Tab may have been closed while connect() was in flight.
        if self._sessions.get(tab_id) is not ssh or ssh.is_aborted:
            logger.info(f'Open connection tab aborted after connect: tab_id={tab_id}')
            self._disconnect_ssh_signals(ssh, terminal)
            await ssh.disconnect()
            ssh.deleteLater()
            return

        logger.info(
            'Open connection tab succeeded: '
            f'tab_id={tab_id}, session_id={session_item.id}, host={session_item.host}'
        )
        if on_connected is not None:
            on_connected()

    async def resize_terminal(self, tab_id: str) -> None:
        ssh = self._sessions.get(tab_id)
        terminal = self._terminals.get(tab_id)
        if ssh is None or terminal is None:
            return
        cols, rows = terminal.terminal_size()
        await ssh.resize(cols, rows)

    async def close_tab(self, tab_id: str) -> None:
        logger.info(f'Close connection tab: tab_id={tab_id}')
        ssh = self._sessions.pop(tab_id, None)
        terminal = self._terminals.pop(tab_id, None)
        self._tab_titles.pop(tab_id, None)
        self._remote_cache.pop(tab_id, None)
        for key in list(self._refresh_tasks):
            if key[0] == tab_id:
                task = self._refresh_tasks.pop(key, None)
                if task is not None and not task.done():
                    task.cancel()
        if ssh is not None:
            ssh.request_abort()
            self._disconnect_ssh_signals(ssh, terminal)
            await ssh.disconnect()
            ssh.deleteLater()
        logger.info(f'Connection tab closed: tab_id={tab_id}')

    def _discard_failed_session(
        self,
        tab_id: str,
        ssh: SSHSession,
        terminal: TerminalPort | None,
    ) -> None:
        if self._sessions.get(tab_id) is ssh:
            self._sessions.pop(tab_id, None)
            self._terminals.pop(tab_id, None)
            self._tab_titles.pop(tab_id, None)
            self._remote_cache.pop(tab_id, None)
        self._disconnect_ssh_signals(ssh, terminal)
        ssh.deleteLater()

    def _discard_disconnected_session(
        self,
        tab_id: str,
        ssh: SSHSession,
        terminal: TerminalPort | None,
    ) -> None:
        if self._sessions.get(tab_id) is not ssh:
            return
        logger.info(f'Remote session disconnected, discarding tab state: tab_id={tab_id}')
        self._sessions.pop(tab_id, None)
        self._terminals.pop(tab_id, None)
        self._tab_titles.pop(tab_id, None)
        self._remote_cache.pop(tab_id, None)
        for key in list(self._refresh_tasks):
            if key[0] == tab_id:
                task = self._refresh_tasks.pop(key, None)
                if task is not None and not task.done():
                    task.cancel()
        self._disconnect_ssh_signals(ssh, terminal)
        ssh.deleteLater()

    @staticmethod
    def _disconnect_ssh_signals(ssh: SSHSession, terminal) -> None:
        for signal in (ssh.data_received, ssh.disconnected, ssh.error):
            try:
                signal.disconnect()
            except TypeError:
                pass
        if terminal is not None:
            try:
                terminal.input_received.disconnect()
            except (TypeError, RuntimeError):
                pass

    async def close_all(self) -> None:
        for tab_id in list(self._sessions.keys()):
            await self.close_tab(tab_id)
