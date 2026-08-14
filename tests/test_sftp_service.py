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


if __name__ == '__main__':
    unittest.main()
