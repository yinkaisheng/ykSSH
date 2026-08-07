#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncssh

from models.session_item import SessionItem
from core.ssh_session import SSHSession
from storage.host_key_store import HostKeyStore


class SSHSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_key_probe_uses_supported_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            probe = AsyncMock(side_effect=RuntimeError('probe stopped'))
            session = SSHSession(
                host_key_store=HostKeyStore(Path(temp_dir) / 'host_keys.json')
            )
            item = SessionItem(host='example.com', port=2222, username='user')

            with patch('core.ssh_session.asyncssh.get_server_host_key', probe):
                with self.assertRaisesRegex(RuntimeError, 'probe stopped'):
                    await session.connect(item)

            probe.assert_awaited_once_with('example.com', 2222)

    async def test_trusted_key_is_passed_directly_to_real_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            store = HostKeyStore(Path(temp_dir) / 'host_keys.json')
            store.trust('192.0.2.10', 22, key)
            session = SSHSession(host_key_store=store)
            item = SessionItem(host='192.0.2.10', port=22, username='user')
            connect = AsyncMock(side_effect=RuntimeError('connect stopped'))

            with patch(
                'core.ssh_session.asyncssh.get_server_host_key',
                AsyncMock(return_value=key),
            ), patch('core.ssh_session.asyncssh.connect', connect):
                with self.assertRaisesRegex(RuntimeError, 'connect stopped'):
                    await session.connect(item)

            known_hosts = connect.await_args.kwargs['known_hosts']
            trusted, *_rest = asyncssh.match_known_hosts(
                known_hosts, '192.0.2.10', '192.0.2.10', None
            )
            self.assertEqual(trusted, [key])


if __name__ == '__main__':
    unittest.main()
