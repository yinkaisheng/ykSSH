#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import asyncssh

from models.session_item import SessionItem
from core.connection_manager import ConnectionManager
from core.ssh_session import SSHConnection, SSHSession
from storage.host_key_store import HostKeyStore


class _BlockingReader:
    def __init__(self) -> None:
        self._released = asyncio.Event()

    async def read(self, _size: int) -> str:
        await self._released.wait()
        return ''


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = _BlockingReader()
        self.stdin = Mock()
        self.closed = False
        self.terminal_sizes: list[tuple[int, int]] = []

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def change_terminal_size(self, cols: int, rows: int) -> None:
        self.terminal_sizes.append((cols, rows))


class _FakeSftp:
    def __init__(self) -> None:
        self.exited = False

    def exit(self) -> None:
        self.exited = True


class _FakeAsyncSSHConnection:
    def __init__(self) -> None:
        self.processes: list[_FakeProcess] = []
        self.sftp_clients: list[_FakeSftp] = []
        self._closing = False
        self._closed = asyncio.Event()

    async def create_process(self, *_args, **_kwargs) -> _FakeProcess:
        process = _FakeProcess()
        self.processes.append(process)
        return process

    async def start_sftp_client(self) -> _FakeSftp:
        sftp = _FakeSftp()
        self.sftp_clients.append(sftp)
        return sftp

    @property
    def closed(self) -> bool:
        return self._closing

    def is_closed(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True
        self._closed.set()

    async def wait_closed(self) -> None:
        await self._closed.wait()


class _FakeSignal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def disconnect(self) -> None:
        self._slots.clear()


class _FakeTerminal:
    def __init__(self) -> None:
        self.input_received = _FakeSignal()
        self.output: list[str] = []

    def terminal_size(self) -> tuple[int, int]:
        return 80, 24

    def write_text(self, text: str) -> None:
        self.output.append(text)


class SSHSessionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _connection(
        item: SessionItem,
        store: HostKeyStore,
        *,
        on_connection_lost=None,
    ) -> SSHConnection:
        callback = on_connection_lost or AsyncMock()
        return SSHConnection(
            item,
            password=None,
            host_key_store=store,
            host_key_confirm=None,
            on_connection_lost=callback,
        )

    async def test_host_key_probe_uses_supported_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            probe = AsyncMock(side_effect=RuntimeError('probe stopped'))
            item = SessionItem(host='example.com', port=2222, username='user')
            connection = self._connection(
                item,
                HostKeyStore(Path(temp_dir) / 'host_keys.json'),
            )

            with patch('core.ssh_session.asyncssh.get_server_host_key', probe):
                with self.assertRaisesRegex(RuntimeError, 'probe stopped'):
                    await connection.acquire('test-tab')

            probe.assert_awaited_once_with('example.com', 2222)

    async def test_trusted_key_is_passed_directly_to_real_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.10', 22, key)
            item = SessionItem(host='192.0.2.10', port=22, username='user')
            connection = self._connection(item, store)
            connect = AsyncMock(side_effect=RuntimeError('connect stopped'))

            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ), patch('core.ssh_session.asyncssh.connect', connect):
                with self.assertRaisesRegex(RuntimeError, 'connect stopped'):
                    await connection.acquire('test-tab')

            known_hosts = connect.await_args.kwargs['known_hosts']
            trusted, *_rest = asyncssh.match_known_hosts(
                known_hosts, '192.0.2.10', '192.0.2.10', None
            )
            self.assertEqual(trusted, [key])

    async def test_shared_connection_uses_creation_time_session_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.11', 22, key)
            item = SessionItem(id='shared', host='192.0.2.11', username='original')
            connection = self._connection(item, store)
            item.host = '192.0.2.12'
            item.username = 'edited'
            connect = AsyncMock(side_effect=RuntimeError('connect stopped'))

            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ) as probe, patch('core.ssh_session.asyncssh.connect', connect):
                with self.assertRaisesRegex(RuntimeError, 'connect stopped'):
                    await connection.acquire('test-tab')

            probe.assert_awaited_once_with('192.0.2.11', 22)
            self.assertEqual(connect.await_args.kwargs['host'], '192.0.2.11')
            self.assertEqual(connect.await_args.kwargs['username'], 'original')

    async def test_two_tabs_share_connection_but_have_independent_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.20', 22, key)
            item = SessionItem(id='shared', host='192.0.2.20', username='user')
            connection = self._connection(item, store)
            raw_connection = _FakeAsyncSSHConnection()
            connect = AsyncMock(return_value=raw_connection)
            first = SSHSession('tab-a')
            second = SSHSession('tab-b')

            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ), patch('core.ssh_session.asyncssh.connect', connect):
                await first.connect(item, connection)
                await second.connect(item, connection)

            connect.assert_awaited_once()
            self.assertEqual(connection.member_count, 2)
            self.assertEqual(len(raw_connection.processes), 2)
            self.assertEqual(len(raw_connection.sftp_clients), 2)
            self.assertIsNot(first.get_sftp(), second.get_sftp())

            await first.disconnect()

            self.assertFalse(raw_connection.closed)
            self.assertEqual(sum(process.closed for process in raw_connection.processes), 1)
            self.assertEqual(sum(sftp.exited for sftp in raw_connection.sftp_clients), 1)
            self.assertTrue(second.is_connected)

            await second.disconnect()

            self.assertTrue(raw_connection.closed)
            self.assertTrue(connection.is_closed)

    async def test_cancelled_waiter_does_not_cancel_shared_connect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.30', 22, key)
            item = SessionItem(id='shared', host='192.0.2.30', username='user')
            connection = self._connection(item, store)
            raw_connection = _FakeAsyncSSHConnection()
            connect_started = asyncio.Event()
            allow_connect = asyncio.Event()

            async def _connect(**_kwargs):
                connect_started.set()
                await allow_connect.wait()
                return raw_connection

            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ), patch('core.ssh_session.asyncssh.connect', AsyncMock(side_effect=_connect)) as connect:
                first = asyncio.create_task(connection.acquire('tab-a'))
                second = asyncio.create_task(connection.acquire('tab-b'))
                await connect_started.wait()
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                self.assertFalse(second.done())

                allow_connect.set()
                self.assertIs(await second, raw_connection)

            connect.assert_awaited_once()
            self.assertEqual(connection.member_count, 1)
            await connection.release('tab-b')

    async def test_shared_connect_failure_reaches_every_waiting_tab(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.31', 22, key)
            item = SessionItem(id='shared', host='192.0.2.31', username='user')
            connection = self._connection(item, store)
            connect_started = asyncio.Event()
            fail_connect = asyncio.Event()

            async def _connect(**_kwargs):
                connect_started.set()
                await fail_connect.wait()
                raise RuntimeError('authentication failed')

            first = SSHSession('tab-a')
            second = SSHSession('tab-b')
            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ), patch(
                'core.ssh_session.asyncssh.connect',
                AsyncMock(side_effect=_connect),
            ) as connect:
                first_task = asyncio.create_task(first.connect(item, connection))
                await connect_started.wait()
                second_task = asyncio.create_task(second.connect(item, connection))
                await asyncio.sleep(0)
                self.assertEqual(connection.member_count, 2)

                fail_connect.set()
                results = await asyncio.gather(
                    first_task,
                    second_task,
                    return_exceptions=True,
                )

            connect.assert_awaited_once()
            self.assertEqual(
                [str(result) for result in results],
                ['authentication failed', 'authentication failed'],
            )
            self.assertTrue(all(isinstance(result, RuntimeError) for result in results))
            self.assertEqual(connection.member_count, 0)
            self.assertTrue(connection.is_closed)
            self.assertFalse(first.is_connected)
            self.assertFalse(second.is_connected)

    async def test_unexpected_connection_close_invokes_group_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.40', 22, key)
            item = SessionItem(id='shared', host='192.0.2.40', username='user')
            lost = asyncio.Event()
            seen: list[SSHConnection] = []

            async def _on_lost(connection: SSHConnection) -> None:
                seen.append(connection)
                lost.set()

            connection = self._connection(item, store, on_connection_lost=_on_lost)
            raw_connection = _FakeAsyncSSHConnection()
            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ), patch(
                'core.ssh_session.asyncssh.connect',
                AsyncMock(return_value=raw_connection),
            ):
                await connection.acquire('tab-a')

            raw_connection.close()
            await asyncio.wait_for(lost.wait(), timeout=1)

            self.assertEqual(seen, [connection])
            self.assertTrue(connection.is_closed)

    async def test_connection_manager_pools_only_matching_session_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.50', 22, key)
            manager = ConnectionManager(credential_store=Mock(get_password=Mock(return_value=None)))
            manager.host_keys = store
            first_item = SessionItem(id='session-a', host='192.0.2.50', username='user')
            second_item = SessionItem(id='session-b', host='192.0.2.50', username='user')
            raw_connections = [_FakeAsyncSSHConnection(), _FakeAsyncSSHConnection()]
            connect = AsyncMock(side_effect=raw_connections)

            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ), patch('core.ssh_session.asyncssh.connect', connect):
                await manager.open_tab('tab-a', first_item, _FakeTerminal())
                await manager.open_tab('tab-b', first_item, _FakeTerminal())
                await manager.open_tab('tab-c', second_item, _FakeTerminal())

            self.assertEqual(connect.await_count, 2)
            self.assertIs(
                manager.get_session('tab-a').shared_connection,
                manager.get_session('tab-b').shared_connection,
            )
            self.assertIsNot(
                manager.get_session('tab-a').shared_connection,
                manager.get_session('tab-c').shared_connection,
            )
            await manager.close_all()


if __name__ == '__main__':
    unittest.main()
