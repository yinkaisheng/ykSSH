#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import asyncssh

from core.sftp_service import (
    LocalSymlinkUnsupported,
    _ensure_local_dir,
    _ensure_remote_dir,
    _sftp_log_context,
    listdir,
    upload,
)


class _ExistingRemoteDirectory:
    def __init__(self) -> None:
        self.deleted = False
        self.created = False

    async def lstat(self, _path: str) -> asyncssh.SFTPAttrs:
        return asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755)

    async def makedirs(self, _path: str, *, exist_ok: bool) -> None:
        del exist_ok
        self.created = True


class SftpMergeTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_remote_directory_is_merged_without_prompt(self) -> None:
        sftp = _ExistingRemoteDirectory()

        async def conflict(*_args):
            self.fail('directory merge must not ask for an overwrite decision')

        with _sftp_log_context('test-tab'):
            result = await _ensure_remote_dir(sftp, '/target', 'source', conflict)
        self.assertTrue(result)
        self.assertFalse(sftp.created)

    async def test_existing_local_directory_is_preserved_for_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / 'target'
            target.mkdir()
            marker = target / 'keep.txt'
            marker.write_text('keep', encoding='utf-8')

            async def conflict(*_args):
                self.fail('directory merge must not ask for an overwrite decision')

            with _sftp_log_context('test-tab'):
                result = await _ensure_local_dir(None, '/source', str(target), conflict)
            self.assertTrue(result)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')

    async def test_selected_local_symlink_is_rejected(self) -> None:
        with patch('core.sftp_service._is_local_link', return_value=True):
            with self.assertRaises(LocalSymlinkUnsupported):
                await upload(None, 'local-link', '/target', tab_id='test-tab')


class SftpDirectoryListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_listing_uses_batched_attributes_without_per_entry_lstat(self) -> None:
        class Directory:
            def __init__(self) -> None:
                self.scandir_calls = 0
                self.lstat_calls = 0
                self.stat_paths: list[str] = []
                self.attrs = {
                    '.': asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755),
                    '..': asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755),
                    'z-file': asyncssh.SFTPAttrs(permissions=stat.S_IFREG | 0o644, size=123, mtime=42),
                    'a-dir': asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755),
                    'b-link': asyncssh.SFTPAttrs(permissions=stat.S_IFLNK | 0o777),
                    'c-broken': asyncssh.SFTPAttrs(permissions=stat.S_IFLNK | 0o777),
                }

            async def listdir(self, _path: str) -> list[str]:
                return list(self.attrs)

            async def scandir(self, _path: str):
                self.scandir_calls += 1
                for name, attrs in self.attrs.items():
                    yield asyncssh.SFTPName(filename=name, attrs=attrs)

            async def lstat(self, path: str) -> asyncssh.SFTPAttrs:
                self.lstat_calls += 1
                return self.attrs[path.rsplit('/', 1)[-1]]

            async def stat(self, path: str) -> asyncssh.SFTPAttrs:
                self.stat_paths.append(path)
                if path.endswith('/c-broken'):
                    raise asyncssh.SFTPNoSuchFile('broken link')
                return asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755)

        sftp = Directory()
        entries = await listdir(sftp, '/target/', tab_id='test-tab')

        self.assertEqual(sftp.lstat_calls, 0, '目录读取已返回属性，不能再次逐项查询')
        self.assertEqual(sftp.scandir_calls, 1)
        self.assertEqual([entry['name'] for entry in entries], ['a-dir', 'b-link', 'c-broken', 'z-file'])
        self.assertEqual([entry['is_dir'] for entry in entries], [True, True, False, False])
        self.assertEqual(sftp.stat_paths, ['/target/b-link', '/target/c-broken'])
        self.assertEqual(entries[-1], {
            'name': 'z-file', 'is_dir': False, 'size': 123, 'mtime': 42.0,
            'perm': '-rw-r--r--', 'mode': stat.S_IFREG | 0o644,
        })


if __name__ == '__main__':
    unittest.main()
