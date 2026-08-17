#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import Any, Awaitable, Callable

import asyncssh
from PyQt5.QtCore import QObject, pyqtSignal

from log_util import logger
from models.session_item import AUTH_PASSWORD, AUTH_PUBLIC_KEY, SessionItem
from storage.host_key_store import HostKeyStore

_SSH_CONNECT_TIMEOUT_SECONDS = 15.0
HostKeyConfirm = Callable[[str, int, str, str], Awaitable[bool]]
ConnectionLostCallback = Callable[['SSHConnection'], Awaitable[None]]


class HostKeyChangedError(RuntimeError):
    def __init__(self, host: str, port: int, expected: str, actual: str) -> None:
        super().__init__(f'SSH host key changed for {host}:{port}: {expected} -> {actual}')
        self.host = host
        self.port = port
        self.expected = expected
        self.actual = actual


class HostKeyRejectedError(RuntimeError):
    """The user declined the first-seen SSH server key."""


class SSHConnection:
    """A shared authenticated connection for all tabs of one saved Session."""

    def __init__(
        self,
        session_item: SessionItem,
        *,
        password: str | None,
        host_key_store: HostKeyStore,
        host_key_confirm: HostKeyConfirm | None,
        on_connection_lost: ConnectionLostCallback,
    ) -> None:
        self.session_item = replace(session_item)
        self._password = password
        self._host_key_store = host_key_store
        self._host_key_confirm = host_key_confirm
        self._on_connection_lost = on_connection_lost
        self._conn: asyncssh.SSHClientConnection | None = None
        self._connect_task: asyncio.Task[asyncssh.SSHClientConnection] | None = None
        self._monitor_task: asyncio.Task | None = None
        self._members: set[str] = set()
        self._closing = False
        self._closed = False
        self._connection_lost = False

    @property
    def session_id(self) -> str:
        return self.session_item.id

    @property
    def member_count(self) -> int:
        return len(self._members)

    @property
    def is_connected(self) -> bool:
        return (
            self._conn is not None
            and not self._conn.is_closed()
            and not self._closed
        )

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def acquire(self, tab_id: str) -> asyncssh.SSHClientConnection:
        """Attach a tab, sharing any in-flight authentication with other tabs."""
        if self._closed or self._connection_lost:
            raise RuntimeError('SSH connection is closed')
        self._members.add(tab_id)
        if self.is_connected:
            logger.info(
                f'SSH connection reused: tab_id={tab_id}, session_id={self.session_id}, '
                f'members={self.member_count}'
            )
            assert self._conn is not None
            return self._conn

        if self._connect_task is None:
            self._connect_task = asyncio.create_task(self._connect(tab_id))
        try:
            return await asyncio.shield(self._connect_task)
        except asyncio.CancelledError:
            await self.release(tab_id)
            raise
        except Exception:
            await self.release(tab_id)
            raise

    async def release(self, tab_id: str) -> None:
        self._members.discard(tab_id)
        if not self._members and not self._connection_lost:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        if self._closing:
            monitor = self._monitor_task
            if monitor is not None and monitor is not asyncio.current_task():
                await asyncio.gather(monitor, return_exceptions=True)
            return

        self._closing = True
        try:
            connect_task = self._connect_task
            if connect_task is not None and not connect_task.done():
                connect_task.cancel()
            if connect_task is not None:
                await asyncio.gather(connect_task, return_exceptions=True)

            conn = self._conn
            if conn is not None:
                try:
                    conn.close()
                    await conn.wait_closed()
                except Exception as exc:
                    logger.warning(
                        f'SSH shared connection close error: session_id={self.session_id}, '
                        f'error={exc}'
                    )

            monitor = self._monitor_task
            if monitor is not None and monitor is not asyncio.current_task():
                await asyncio.gather(monitor, return_exceptions=True)
            self._conn = None
            self._closed = True
            logger.info(f'SSH shared connection closed: session_id={self.session_id}')
        finally:
            self._password = None
            self._host_key_confirm = None
            self._closing = False

    async def _connect(self, tab_id: str) -> asyncssh.SSHClientConnection:
        session_item = self.session_item
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
        elif session_item.auth_type == AUTH_PASSWORD and self._password:
            options['password'] = self._password

        logger.info(
            'SSH connecting: '
            f'tab_id={tab_id}, session_id={session_item.id}, name={session_item.name}, '
            f'host={session_item.host}, port={session_item.port}, '
            f'username={session_item.username}, auth_type={session_item.auth_type}'
        )
        try:
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
                if self._host_key_confirm is not None:
                    accepted = await self._host_key_confirm(
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

            # The key was checked above. Passing it directly also handles the
            # default-port representation used by AsyncSSH known-host matching.
            options['known_hosts'] = ([server_key], [], [])
            conn = await asyncssh.connect(**options)
            self._conn = conn
            self._monitor_task = asyncio.create_task(self._monitor_connection(conn))
            logger.info(
                'SSH shared connection established: '
                f'tab_id={tab_id}, session_id={session_item.id}, name={session_item.name}, '
                f'host={session_item.host}, port={session_item.port}'
            )
            return conn
        except asyncio.CancelledError:
            logger.info(
                f'SSH shared connection cancelled: tab_id={tab_id}, '
                f'session_id={session_item.id}'
            )
            raise
        except Exception as exc:
            logger.warning(
                'SSH shared connection failed: '
                f'tab_id={tab_id}, session_id={session_item.id}, name={session_item.name}, '
                f'host={session_item.host}, port={session_item.port}, error={exc}'
            )
            raise
        finally:
            self._password = None
            self._host_key_confirm = None

    async def _monitor_connection(self, conn: asyncssh.SSHClientConnection) -> None:
        try:
            await conn.wait_closed()
            if self._closing or self._conn is not conn:
                return
            self._connection_lost = True
            self._closed = True
            logger.warning(
                f'SSH shared connection lost: session_id={self.session_id}, '
                f'members={self.member_count}'
            )
            await self._on_connection_lost(self)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f'SSH shared connection monitor failed: session_id={self.session_id}, error={exc}'
            )


class SSHSession(QObject):
    """Per-tab shell and SFTP channels on a shared SSH connection."""

    connected = pyqtSignal()
    disconnected = pyqtSignal()
    data_received = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        tab_id: str,
        parent: QObject = None,
    ) -> None:
        super().__init__(parent)
        self.tab_id = tab_id
        self._connection: SSHConnection | None = None
        self._attached = False
        self._process: asyncssh.SSHClientProcess | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._read_task: asyncio.Task | None = None
        self._disconnect_lock = asyncio.Lock()
        self._aborted = False
        self._session_item: SessionItem | None = None
        self._cols = 80
        self._rows = 24

    @property
    def session_item(self) -> SessionItem | None:
        return self._session_item

    @property
    def shared_connection(self) -> SSHConnection | None:
        return self._connection

    @property
    def is_connected(self) -> bool:
        return (
            self._attached
            and self._process is not None
            and self._connection is not None
            and self._connection.is_connected
        )

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    def request_abort(self) -> None:
        """Mark connect/in-flight work as aborted (e.g. tab closed while connecting)."""
        self._aborted = True

    async def connect(
        self,
        session_item: SessionItem,
        connection: SSHConnection,
        *,
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        if self.is_connected:
            await self.disconnect()

        self._aborted = False
        self._session_item = session_item
        self._connection = connection
        self._cols = cols
        self._rows = rows

        try:
            conn = await connection.acquire(self.tab_id)
            self._attached = True
            if self._aborted:
                await self.disconnect()
                raise asyncio.CancelledError()

            self._process = await conn.create_process(
                '',
                term_type='xterm-256color',
                term_size=(cols, rows),
                encoding='utf-8',
            )
            if self._aborted:
                await self.disconnect()
                raise asyncio.CancelledError()

            self._sftp = await conn.start_sftp_client()
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
        async with self._disconnect_lock:
            self._aborted = True
            session_item = self._session_item
            if session_item is not None:
                logger.info(
                    'SSH disconnecting: '
                    f'tab_id={self.tab_id}, session_id={session_item.id}, name={session_item.name}, '
                    f'host={session_item.host}, port={session_item.port}'
                )

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

            if self._attached and self._connection is not None:
                self._attached = False
                await self._connection.release(self.tab_id)

            self.disconnected.emit()
            if session_item is not None:
                logger.info(
                    'SSH disconnected: '
                    f'tab_id={self.tab_id}, session_id={session_item.id}, name={session_item.name}, '
                    f'host={session_item.host}, port={session_item.port}'
                )

    async def _read_loop(self) -> None:
        assert self._process is not None
        cancelled = False
        try:
            while True:
                data = await self._process.stdout.read(4096)
                if not data:
                    break
                self.data_received.emit(data)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            logger.warning(f'SSH read loop error: tab_id={self.tab_id}, error={exc}')
            self.error.emit(str(exc))
        finally:
            if not cancelled and self._attached:
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
