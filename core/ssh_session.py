#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import Any, Awaitable, Callable

import asyncssh
from PyQt5.QtCore import QObject, pyqtSignal

from core.ssh_config import SshConfigError, SshConfigHost, resolve_session_hops
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
        hop_passwords: dict[str, str] | None = None,
    ) -> None:
        self.session_item = replace(session_item)
        self._password = password
        self._hop_passwords = dict(hop_passwords or {})
        self._host_key_store = host_key_store
        self._host_key_confirm = host_key_confirm
        self._on_connection_lost = on_connection_lost
        self._conn: asyncssh.SSHClientConnection | None = None
        self._hop_conns: list[asyncssh.SSHClientConnection] = []
        self._hop_monitor_tasks: list[asyncio.Task] = []
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
            await self._close_hop_connections()
            await self._await_hop_monitors()

            monitor = self._monitor_task
            if monitor is not None and monitor is not asyncio.current_task():
                await asyncio.gather(monitor, return_exceptions=True)
            self._conn = None
            self._closed = True
            logger.info(f'SSH shared connection closed: session_id={self.session_id}')
        finally:
            self._password = None
            self._hop_passwords.clear()
            self._host_key_confirm = None
            self._closing = False

    async def _connect(self, tab_id: str) -> asyncssh.SSHClientConnection:
        session_item = self.session_item
        logger.info(
            'SSH connecting: '
            f'tab_id={tab_id}, session_id={session_item.id}, name={session_item.name}, '
            f'host={session_item.host}, port={session_item.port}, '
            f'username={session_item.username}, auth_type={session_item.auth_type}, '
            f'ssh_config={session_item.ssh_config_path or "-"}'
        )
        try:
            if session_item.uses_ssh_config():
                conn = await self._connect_via_ssh_config(tab_id)
            else:
                conn = await self._connect_direct(tab_id)
            self._conn = conn
            self._monitor_task = asyncio.create_task(self._monitor_connection(conn))
            self._hop_monitor_tasks = [
                asyncio.create_task(self._monitor_hop_connection(hop))
                for hop in self._hop_conns
            ]
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
            self._hop_passwords.clear()
            self._host_key_confirm = None

    async def _connect_direct(self, tab_id: str) -> asyncssh.SSHClientConnection:
        session_item = self.session_item
        options = self._base_connect_options(
            session_item.host,
            session_item.port,
            session_item.username,
        )
        if session_item.auth_type == AUTH_PUBLIC_KEY and session_item.key_path:
            key_path = os.path.expanduser(session_item.key_path.strip())
            if key_path:
                options['client_keys'] = [key_path]
        elif session_item.auth_type == AUTH_PASSWORD and self._password:
            options['password'] = self._password
        server_key = await self._trusted_server_key(session_item.host, session_item.port)
        options['known_hosts'] = ([server_key], [], [])
        logger.info(
            f'SSH direct connect: tab_id={tab_id}, session_id={session_item.id}, '
            f'name={session_item.name}, host={session_item.host}, port={session_item.port}'
        )
        return await asyncssh.connect(**options)

    async def _connect_via_ssh_config(self, tab_id: str) -> asyncssh.SSHClientConnection:
        hops = resolve_session_hops(self.session_item)
        if not hops:
            raise SshConfigError(
                'ssh_config.host_not_found',
                alias=self.session_item.host,
                path=self.session_item.ssh_config_path,
            )
        dest = hops[-1]
        self.session_item = replace(
            self.session_item,
            username=dest.username or self.session_item.username,
            port=dest.port,
        )
        opened: list[asyncssh.SSHClientConnection] = []
        try:
            tunnel: asyncssh.SSHClientConnection | None = None
            for index, hop in enumerate(hops):
                conn = await self._connect_hop(tab_id, hop, tunnel=tunnel)
                opened.append(conn)
                tunnel = conn
                logger.info(
                    f'SSH config hop connected: tab_id={tab_id}, '
                    f'session_id={self.session_item.id}, name={self.session_item.name}, '
                    f'hop={index + 1}/{len(hops)}, '
                    f'alias={hop.alias}, host={hop.connect_host}, port={hop.port}'
                )
            self._hop_conns = opened[:-1]
            return opened[-1]
        except (Exception, asyncio.CancelledError):
            await self._close_connections(list(reversed(opened)))
            raise

    async def _connect_hop(
        self,
        tab_id: str,
        hop: SshConfigHost,
        *,
        tunnel: asyncssh.SSHClientConnection | None,
    ) -> asyncssh.SSHClientConnection:
        if not hop.username:
            raise SshConfigError(
                'ssh_config.user_missing',
                alias=hop.alias,
                path=self.session_item.ssh_config_path,
            )
        options = self._base_connect_options(hop.connect_host, hop.port, hop.username)
        if hop.identity_files:
            options['client_keys'] = list(hop.identity_files)
        else:
            password = self._hop_passwords.get(hop.alias)
            if password:
                options['password'] = password
            else:
                raise SshConfigError(
                    'ssh_config.auth_missing',
                    alias=hop.alias,
                    path=self.session_item.ssh_config_path,
                )
        if tunnel is not None:
            options['tunnel'] = tunnel
        server_key = await self._trusted_server_key(
            hop.connect_host,
            hop.port,
            tunnel=tunnel,
        )
        options['known_hosts'] = ([server_key], [], [])
        logger.info(
            f'SSH config hop connecting: tab_id={tab_id}, session_id={self.session_item.id}, '
            f'name={self.session_item.name}, '
            f'alias={hop.alias}, host={hop.connect_host}, port={hop.port}'
        )
        return await asyncssh.connect(**options)

    @staticmethod
    def _base_connect_options(host: str, port: int, username: str) -> dict[str, Any]:
        return {
            'host': host,
            'port': port,
            'username': username,
            'connect_timeout': _SSH_CONNECT_TIMEOUT_SECONDS,
            'login_timeout': _SSH_CONNECT_TIMEOUT_SECONDS,
        }

    async def _trusted_server_key(
        self,
        host: str,
        port: int,
        *,
        tunnel: asyncssh.SSHClientConnection | None = None,
    ) -> Any:
        if tunnel is None:
            server_key = await asyncio.wait_for(
                asyncssh.get_server_host_key(host, port),
                timeout=_SSH_CONNECT_TIMEOUT_SECONDS,
            )
        else:
            server_key = await asyncio.wait_for(
                asyncssh.get_server_host_key(host, port, tunnel=tunnel),
                timeout=_SSH_CONNECT_TIMEOUT_SECONDS,
            )
        if server_key is None:
            raise RuntimeError('SSH server did not present a host key')
        status = self._host_key_store.check(host, port, server_key)
        actual_fingerprint = server_key.get_fingerprint()
        if status == 'changed':
            raise HostKeyChangedError(
                host,
                port,
                self._host_key_store.fingerprint(host, port),
                actual_fingerprint,
            )
        if status == 'unknown':
            accepted = False
            if self._host_key_confirm is not None:
                accepted = await self._host_key_confirm(
                    host,
                    port,
                    server_key.get_algorithm(),
                    actual_fingerprint,
                )
            if not accepted:
                raise HostKeyRejectedError(
                    f'SSH host key was not trusted for {host}:{port}'
                )
            self._host_key_store.trust(host, port, server_key)
        return server_key

    async def _close_hop_connections(self) -> None:
        hops = self._hop_conns
        self._hop_conns = []
        await self._close_connections(list(reversed(hops)))

    async def _await_hop_monitors(self) -> None:
        tasks = self._hop_monitor_tasks
        self._hop_monitor_tasks = []
        current = asyncio.current_task()
        pending = [task for task in tasks if task is not current and not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _monitor_hop_connection(self, hop_conn: asyncssh.SSHClientConnection) -> None:
        """If a jump host drops, close the destination so reconnect UI can start."""
        try:
            await hop_conn.wait_closed()
            if self._closing or self._closed or self._connection_lost:
                return
            logger.warning(
                f'SSH jump hop lost: session_id={self.session_id}, '
                f'name={self.session_item.name}, members={self.member_count}'
            )
            dest = self._conn
            if dest is not None and not dest.is_closed():
                dest.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f'SSH jump hop monitor failed: session_id={self.session_id}, '
                f'name={self.session_item.name}, error={exc}'
            )

    async def _close_connections(self, conns: list[asyncssh.SSHClientConnection]) -> None:
        for conn in conns:
            try:
                conn.close()
                await conn.wait_closed()
            except Exception as exc:
                logger.warning(
                    f'SSH hop connection close error: session_id={self.session_id}, '
                    f'name={self.session_item.name}, error={exc}'
                )

    async def _monitor_connection(self, conn: asyncssh.SSHClientConnection) -> None:
        try:
            await conn.wait_closed()
            if self._closing or self._conn is not conn:
                return
            self._connection_lost = True
            self._closed = True
            await self._close_hop_connections()
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
        self._output_activity = asyncio.Event()
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
        self._output_activity.clear()

        try:
            conn = await connection.acquire(self.tab_id)
            self._session_item = replace(connection.session_item)
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
                self._output_activity.set()
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

    async def wait_for_output_idle(
        self,
        *,
        idle_seconds: float,
        timeout_seconds: float,
    ) -> None:
        """Wait until shell output stays quiet, bounded by an overall timeout."""
        idle_seconds = max(0.0, float(idle_seconds))
        timeout_seconds = max(0.0, float(timeout_seconds))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while idle_seconds > 0:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            self._output_activity.clear()
            try:
                await asyncio.wait_for(
                    self._output_activity.wait(),
                    timeout=min(idle_seconds, remaining),
                )
            except asyncio.TimeoutError:
                return

    async def wait_for_shell_ready(
        self,
        ready: Callable[[], bool],
        *,
        timeout_seconds: float,
    ) -> bool:
        """Wait for output processing to expose the first interactive prompt."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout_seconds))
        while True:
            if ready():
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            self._output_activity.clear()
            if ready():
                return True
            try:
                await asyncio.wait_for(self._output_activity.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                return bool(ready())

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
