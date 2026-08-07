#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import asyncssh

from storage.host_key_store import HostKeyStore


class HostKeyStoreTests(unittest.TestCase):
    def test_trust_reload_and_detect_changed_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'host_keys.json'
            first = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
            second = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()

            store = HostKeyStore(path)
            self.assertEqual(store.check('Example.COM', 22, first), 'unknown')
            store.trust('Example.COM', 22, first)

            reloaded = HostKeyStore(path)
            self.assertEqual(reloaded.check('example.com', 22, first), 'trusted')
            self.assertEqual(reloaded.check('example.com', 22, second), 'changed')
            self.assertEqual(reloaded.fingerprint('example.com', 22), first.get_fingerprint())

            known_hosts = asyncssh.import_known_hosts(
                f'{HostKeyStore.endpoint("example.com", 22)} {HostKeyStore.export_key(first)}\n'
            )
            trusted, *_rest = asyncssh.match_known_hosts(
                known_hosts, 'example.com', '203.0.113.10', 22
            )
            self.assertEqual(len(trusted), 1)
            self.assertTrue(reloaded.has('example.com', 22))
            self.assertTrue(reloaded.forget('example.com', 22))
            self.assertFalse(reloaded.has('example.com', 22))


if __name__ == '__main__':
    unittest.main()
