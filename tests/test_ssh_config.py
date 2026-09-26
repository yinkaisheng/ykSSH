#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core.ssh_config import (
    SshConfigError,
    parse_ssh_config_file,
    resolve_proxy_chain,
    resolve_session_hops,
    session_host_key_endpoints,
)
from models.session_item import SessionItem


_SAMPLE_CONFIG = """
# jump host
Host api-01
    HostName 192.0.2.10
    User root
    IdentityFile ~/.ssh/id_ed25519

Host vod-worker-01
    HostName 192.0.2.20
    User root
    ProxyJump api-01
    IdentityFile ~/.ssh/id_ed25519

Host *
    User ignored
"""


class SshConfigTests(unittest.TestCase):
    def _write_config(self, directory: str, text: str = _SAMPLE_CONFIG) -> Path:
        path = Path(directory) / 'vodconfig'
        path.write_text(text, encoding='utf-8')
        return path

    def test_parse_host_alias_and_proxy_jump(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir)
            hosts = parse_ssh_config_file(path)

        self.assertEqual(list(hosts), ['api-01', 'vod-worker-01'])
        jump = hosts['api-01']
        self.assertEqual(jump.hostname, '192.0.2.10')
        self.assertEqual(jump.username, 'root')
        self.assertEqual(jump.port, 22)
        self.assertTrue(jump.identity_files[0].endswith(os.path.join('.ssh', 'id_ed25519')))
        worker = hosts['vod-worker-01']
        self.assertEqual(worker.proxy_jump, 'api-01')
        self.assertEqual(worker.connect_host, '192.0.2.20')

    def test_resolve_proxy_chain_puts_destination_last(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir)
            hosts = parse_ssh_config_file(path)
            hops = resolve_proxy_chain(hosts, 'vod-worker-01', path=str(path))

        self.assertEqual([hop.alias for hop in hops], ['api-01', 'vod-worker-01'])
        self.assertEqual(hops[0].connect_host, '192.0.2.10')
        self.assertEqual(hops[1].connect_host, '192.0.2.20')
        self.assertEqual(hops[0].proxy_jump, '')
        self.assertEqual(hops[1].proxy_jump, '')

    def test_proxy_jump_cycle_is_rejected(self) -> None:
        text = """
Host left
    HostName 192.0.2.30
    User root
    IdentityFile ~/.ssh/id_ed25519
    ProxyJump right
Host right
    HostName 192.0.2.31
    User root
    IdentityFile ~/.ssh/id_ed25519
    ProxyJump left
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir, text)
            hosts = parse_ssh_config_file(path)
            with self.assertRaises(SshConfigError) as raised:
                resolve_proxy_chain(hosts, 'left', path=str(path))
        self.assertEqual(raised.exception.message_key, 'ssh_config.proxy_cycle')

    def test_missing_alias_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir)
            item = SessionItem(host='missing', ssh_config_path=str(path))
            with self.assertRaises(SshConfigError) as raised:
                resolve_session_hops(item)
        self.assertEqual(raised.exception.message_key, 'ssh_config.host_not_found')

    def test_match_blocks_do_not_change_host_fields(self) -> None:
        text = """
Match host unrelated
    User ignored
Host first
    HostName 192.0.2.10
    User original
    Port 2222
    IdentityFile ~/.ssh/original
Match host other
    HostName 192.0.2.99
    User unrelated
    Port 9999
    IdentityFile ~/.ssh/unrelated
    ProxyJump other
Match all
    User also-ignored
Host second
    HostName 192.0.2.20
    User second-user
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            hosts = parse_ssh_config_file(self._write_config(temp_dir, text))
        self.assertEqual(list(hosts), ['first', 'second'])
        first = hosts['first']
        self.assertEqual(first.connect_host, '192.0.2.10')
        self.assertEqual(first.username, 'original')
        self.assertEqual(first.port, 2222)
        self.assertEqual(len(first.identity_files), 1)
        self.assertTrue(first.identity_files[0].endswith(os.path.join('.ssh', 'original')))
        self.assertEqual(first.proxy_jump, '')
        self.assertEqual(hosts['second'].username, 'second-user')

    def test_proxy_jump_none_disables_jump_without_hiding_host_named_none(self) -> None:
        text = """
Host none
    HostName 192.0.2.10
    User jump-user
Host dest
    HostName 192.0.2.20
    User user
    IdentityFile ~/.ssh/id_ed25519
    ProxyJump none
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir, text)
            item = SessionItem(host='dest', ssh_config_path=str(path))
            self.assertEqual([hop.alias for hop in resolve_session_hops(item)], ['dest'])
            self.assertEqual(session_host_key_endpoints(item), [('192.0.2.20', 22)])
            hosts = parse_ssh_config_file(path)
            self.assertEqual(resolve_proxy_chain(hosts, 'none')[0].connect_host, '192.0.2.10')

    def test_invalid_encoding_is_reported_as_config_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'config'
            path.write_bytes(b'# invalid UTF-8: \xff\nHost dest\n')
            with self.assertRaises(SshConfigError) as raised:
                parse_ssh_config_file(path)
            self.assertEqual(raised.exception.message_key, 'ssh_config.file_unreadable')
            self.assertEqual(raised.exception.params['path'], str(path))
            self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)

    def test_password_jump_config_has_no_identity_file(self) -> None:
        text = """
Host jump-01
    HostName 192.0.2.10
    User jumpuser
Host inner-01
    HostName 10.0.0.8
    User inneruser
    ProxyJump jump-01
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir, text)
            hosts = parse_ssh_config_file(path)
            hops = resolve_proxy_chain(hosts, 'inner-01', path=str(path))
        self.assertEqual([hop.alias for hop in hops], ['jump-01', 'inner-01'])
        self.assertEqual(hops[0].identity_files, ())
        self.assertEqual(hops[1].identity_files, ())
        self.assertEqual(hops[1].proxy_jump, '')

    def test_mixed_identity_and_password_hops_are_parsed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / 'id_ed25519'
            key_path.write_text('dummy-key\n', encoding='utf-8')
            text = f"""
Host jump-01
    HostName 192.0.2.10
    User jumpuser
    IdentityFile {key_path}
Host inner-01
    HostName 10.0.0.8
    User inneruser
    ProxyJump jump-01
"""
            path = self._write_config(temp_dir, text)
            hops = resolve_proxy_chain(parse_ssh_config_file(path), 'inner-01', path=str(path))
        self.assertEqual([hop.alias for hop in hops], ['jump-01', 'inner-01'])
        self.assertTrue(hops[0].identity_files)
        self.assertEqual(hops[1].identity_files, ())

    def test_session_item_roundtrip_keeps_config_path(self) -> None:
        item = SessionItem(
            name='worker',
            host='vod-worker-01',
            ssh_config_path=r'C:\Users\A\.ssh\vodconfig',
            local_path=r'D:\work',
        )
        restored = SessionItem.from_dict(item.to_dict())
        self.assertTrue(item.uses_ssh_config())
        self.assertFalse(item.is_folder())
        self.assertEqual(restored.ssh_config_path, item.ssh_config_path)
        self.assertEqual(restored.host, 'vod-worker-01')
        self.assertNotIn('username', item.to_dict())
        self.assertNotIn('auth_type', item.to_dict())
        self.assertEqual(item.to_dict().get('local_path'), r'D:\work')

    def test_folder_still_requires_empty_host_and_config_path(self) -> None:
        folder = SessionItem(name='group')
        self.assertTrue(folder.is_folder())
        self.assertFalse(SessionItem(host='api-01').is_folder())
        self.assertTrue(folder.expanded)
        self.assertNotIn('expanded', folder.to_dict())
        collapsed = SessionItem.from_dict({'id': 'g', 'name': 'group', 'expanded': False})
        self.assertFalse(collapsed.expanded)
        self.assertEqual(collapsed.to_dict().get('expanded'), False)
        missing = SessionItem.from_dict({'id': 'g', 'name': 'group'})
        self.assertTrue(missing.expanded)

    def test_host_key_endpoints_use_resolved_hostnames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir)
            item = SessionItem(host='vod-worker-01', ssh_config_path=str(path))
            endpoints = session_host_key_endpoints(item)
        self.assertEqual(endpoints, [('192.0.2.10', 22), ('192.0.2.20', 22)])

    def test_quoted_identity_file_and_inline_comment(self) -> None:
        text = """
Host quoted
    HostName 192.0.2.40
    User root
    IdentityFile "~/.ssh/quoted key"  # comment
    Port 2222
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._write_config(temp_dir, text)
            hosts = parse_ssh_config_file(path)
        hop = hosts['quoted']
        self.assertEqual(hop.port, 2222)
        self.assertTrue(hop.identity_files[0].endswith(os.path.join('.ssh', 'quoted key')))


if __name__ == '__main__':
    unittest.main()
