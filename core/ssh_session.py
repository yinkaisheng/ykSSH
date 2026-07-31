#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from typing import Any, List, Optional

import asyncssh
from PyQt5.QtCore import QObject, pyqtSignal

from log_util import logger
from models.session_item import AUTH_PASSWORD, AUTH_PUBLIC_KEY, SessionItem


class SSHSession(QObject):
    """Async SSH shell session with Qt signals."""

    connected = pyqtSignal()
    disconnected = pyqtSignal()
    data_received = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._process: Optional[asyncssh.SSHClientProcess] = None
        self._sftp: Optional[asyncssh.SFTPClient] = None
        self._read_task: Optional[asyncio.Task] = None
        self._disconnecting = False
        self._session_item: Optional[SessionItem] = None
        self._password: Optional[str] = None
        self._cols = 80
        self._rows = 24

    @property
    def session_item(self) -> Optional[SessionItem]:
        return self._session_item

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.is_closing()

    async def connect(
        self,
        session_item: SessionItem,
        *,
        password: Optional[str] = None,
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        if self.is_connected:
            await self.disconnect()

        self._session_item = session_item
        self._password = password
        self._cols = cols
        self._rows = rows

        options: dict[str, Any] = {
            'host': session_item.host,
            'port': session_item.port,
            'username': session_item.username,
            'known_hosts': None,
        }

        if session_item.auth_type == AUTH_PUBLIC_KEY and session_item.key_path:
            key_path = os.path.expanduser(session_item.key_path.strip())
            if key_path:
                options['client_keys'] = [key_path]
        elif session_item.auth_type == AUTH_PASSWORD and password:
            options['password'] = password

        try:
            self._conn = await asyncssh.connect(**options)
            self._process = await self._conn.create_process(
                '',
                term_type='xterm-256color',
                term_size=(cols, rows),
                encoding='utf-8',
            )
            self._sftp = await self._conn.start_sftp_client()
            self._read_task = asyncio.create_task(self._read_loop())
            self.connected.emit()
        except Exception as exc:
            logger.warning(f'SSH connect failed: {exc}')
            await self.disconnect()
            self.error.emit(str(exc))
            raise

    async def disconnect(self) -> None:
        if self._disconnecting:
            return
        self._disconnecting = True
        try:
            if self._read_task is not None:
                self._read_task.cancel()
                try:
                    await self._read_task
                except asyncio.CancelledError:
                    pass
                self._read_task = None

            if self._process is not None:
                self._process.close()
                await self._process.wait_closed()
                self._process = None

            if self._sftp is not None:
                self._sftp.exit()
                self._sftp = None

            if self._conn is not None:
                self._conn.close()
                await self._conn.wait_closed()
                self._conn = None

            self.disconnected.emit()
        finally:
            self._disconnecting = False

    async def _read_loop(self) -> None:
        assert self._process is not None
        try:
            while True:
                data = await self._process.stdout.read(4096)
                if not data:
                    break
                self.data_received.emit(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f'SSH read loop error: {exc}')
            self.error.emit(str(exc))
        finally:
            if self._conn is not None and not self._disconnecting:
                await self.disconnect()

    def write(self, data: bytes) -> None:
        if self._process is None or self._process.stdin is None:
            return
        try:
            text = data.decode('utf-8', errors='replace')
            self._process.stdin.write(text)
        except Exception as exc:
            logger.warning(f'SSH write failed: {exc}')

    async def resize(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        if self._process is not None:
            self._process.change_terminal_size(cols, rows)

    def get_sftp(self) -> Optional[asyncssh.SFTPClient]:
        return self._sftp
