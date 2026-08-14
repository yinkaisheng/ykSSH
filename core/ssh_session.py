#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

import asyncssh
from PyQt5.QtCore import QObject, pyqtSignal

from log_util import logger
from models.session_item import AUTH_PASSWORD, AUTH_PUBLIC_KEY, SessionItem
from storage.host_key_store import HostKeyStore

_SSH_CONNECT_TIMEOUT_SECONDS = 15.0
HostKeyConfirm = Callable[[str, int, str, str], Awaitable[bool]]


class HostKeyChangedError(RuntimeError):
    def __init__(self, host: str, port: int, expected: str, actual: str) -> None:
        super().__init__(f'SSH host key changed for {host}:{port}: {expected} -> {actual}')
        self.host = host
        self.port = port
        self.expected = expected
        self.actual = actual


class HostKeyRejectedError(RuntimeError):
    """The user declined the first-seen SSH server key."""


class SSHSession(QObject):
    """Async SSH shell session with Qt signals."""

    connected = pyqtSignal()
    disconnected = pyqtSignal()
    data_received = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        tab_id: str,
        parent: QObject = None,
        host_key_store: HostKeyStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.tab_id = tab_id
        self._conn: asyncssh.SSHClientConnection | None = None
        self._process: asyncssh.SSHClientProcess | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._read_task: asyncio.Task | None = None
        self._disconnecting = False
        self._aborted = False
        self._session_item: SessionItem | None = None
        self._cols = 80
        self._rows = 24
        self._host_key_store = host_key_store or HostKeyStore()

    @property
    def session_item(self) -> SessionItem | None:
        return self._session_item

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.is_closing()

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    def request_abort(self) -> None:
        """Mark connect/in-flight work as aborted (e.g. tab closed while connecting)."""
        self._aborted = True

    async def connect(
        self,
        session_item: SessionItem,
        *,
        password: str | None = None,
        cols: int = 80,
        rows: int = 24,
        host_key_confirm: HostKeyConfirm | None = None,
    ) -> None:
        if self.is_connected:
            await self.disconnect()

        self._aborted = False
        self._session_item = session_item
        self._cols = cols
        self._rows = rows

        options: dict[str, Any] = {
            'host': session_item.host,
            'port': session_item.port,
            'username': session_item.username,
            'connect_timeout': _SSH_CONNECT_TIMEOUT_SECONDS,
            'login_timeout': _SSH_CONNECT_TIMEOUT_SECONDS,
        }

        if session_item.auth_type == AUTH_PUBLIC_KEY and session_item.key_path:
            key_path = os.path.expanduser(session_item.key_path.strip())
            if key_path:
                options['client_keys'] = [key_path]
        elif session_item.auth_type == AUTH_PASSWORD and password:
            options['password'] = password

        try:
            logger.info(
                'SSH connecting: '
                f'tab_id={self.tab_id}, session_id={session_item.id}, name={session_item.name}, '
                f'host={session_item.host}, port={session_item.port}, '
                f'username={session_item.username}, auth_type={session_item.auth_type}'
            )
            server_key = await asyncio.wait_for(
                asyncssh.get_server_host_key(session_item.host, session_item.port),
                timeout=_SSH_CONNECT_TIMEOUT_SECONDS,
            )
            if server_key is None:
                raise RuntimeError('SSH server did not present a host key')
            status = self._host_key_store.check(session_item.host, session_item.port, server_key)
            actual_fingerprint = server_key.get_fingerprint()
            if status == 'changed':
                raise HostKeyChangedError(
                    session_item.host,
                    session_item.port,
                    self._host_key_store.fingerprint(session_item.host, session_item.port),
                    actual_fingerprint,
                )
            if status == 'unknown':
                accepted = False
                if host_key_confirm is not None:
                    accepted = await host_key_confirm(
                        session_item.host,
                        session_item.port,
                        server_key.get_algorithm(),
                        actual_fingerprint,
                    )
                if not accepted:
                    raise HostKeyRejectedError(
                        f'SSH host key was not trusted for {session_item.host}:{session_item.port}'
                    )
                self._host_key_store.trust(session_item.host, session_item.port, server_key)

            # This key was already checked against HostKeyStore above. Passing
            # it directly also works when AsyncSSH represents default port 22
            # as ``None`` during known-host matching.
            options['known_hosts'] = ([server_key], [], [])
            self._conn = await asyncssh.connect(**options)
            if self._aborted:
                await self.disconnect()
                raise asyncio.CancelledError()

            self._process = await self._conn.create_process(
                '',
                term_type='xterm-256color',
                term_size=(cols, rows),
                encoding='utf-8',
            )
            if self._aborted:
                await self.disconnect()
                raise asyncio.CancelledError()

            self._sftp = await self._conn.start_sftp_client()
            if self._aborted:
                await self.disconnect()
                raise asyncio.CancelledError()

            self._read_task = asyncio.create_task(self._read_loop())
            logger.info(
                'SSH connected: '
                f'tab_id={self.tab_id}, session_id={session_item.id}, name={session_item.name}, '
                f'host={session_item.host}, '
                f'port={session_item.port}, cols={cols}, rows={rows}'
            )
            self.connected.emit()
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        except Exception as exc:
            logger.warning(
                'SSH connect failed: '
                f'tab_id={self.tab_id}, session_id={session_item.id}, name={session_item.name}, '
                f'host={session_item.host}, '
                f'port={session_item.port}, error={exc}'
            )
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        if self._disconnecting:
            return
        self._disconnecting = True
        self._aborted = True
        session_item = self._session_item
        if session_item is not None:
            logger.info(
                'SSH disconnecting: '
                f'tab_id={self.tab_id}, session_id={session_item.id}, name={session_item.name}, '
                f'host={session_item.host}, port={session_item.port}'
            )
        try:
            current_task = asyncio.current_task()
            if self._read_task is not None and self._read_task is not current_task:
                self._read_task.cancel()
                try:
                    await self._read_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.warning(f'SSH read task cleanup error: tab_id={self.tab_id}, error={exc}')
                self._read_task = None

            if self._process is not None:
                try:
                    self._process.close()
                    await self._process.wait_closed()
                except Exception as exc:
                    logger.warning(f'SSH process close error: tab_id={self.tab_id}, error={exc}')
                self._process = None

            if self._sftp is not None:
                try:
                    self._sftp.exit()
                except Exception as exc:
                    logger.warning(f'SFTP exit error: tab_id={self.tab_id}, error={exc}')
                self._sftp = None

            if self._conn is not None:
                try:
                    self._conn.close()
                    await self._conn.wait_closed()
                except Exception as exc:
                    logger.warning(f'SSH connection close error: tab_id={self.tab_id}, error={exc}')
                self._conn = None

            self.disconnected.emit()
            if session_item is not None:
                logger.info(
                    'SSH disconnected: '
                    f'tab_id={self.tab_id}, session_id={session_item.id}, name={session_item.name}, '
                    f'host={session_item.host}, port={session_item.port}'
                )
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
            logger.warning(f'SSH read loop error: tab_id={self.tab_id}, error={exc}')
            self.error.emit(str(exc))
        finally:
            if self._conn is not None and not self._disconnecting:
                await self.disconnect()
            if self._read_task is asyncio.current_task():
                self._read_task = None

    def write(self, data: bytes) -> None:
        if self._process is None or self._process.stdin is None:
            return
        try:
            text = data.decode('utf-8', errors='replace')
            self._process.stdin.write(text)
        except Exception as exc:
            logger.warning(f'SSH write failed: tab_id={self.tab_id}, error={exc}')

    async def resize(self, cols: int, rows: int) -> None:
        self._cols = cols
        self._rows = rows
        if self._process is not None:
            self._process.change_terminal_size(cols, rows)

    def get_sftp(self) -> asyncssh.SFTPClient | None:
        return self._sftp
