#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import stat
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import asyncssh

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from core.connection_manager import ConnectionManager
from core.sftp_service import listdir
from ui.file_panel.panels import RemoteFilePanel
from ui.sftp_ui_handler import SftpUiHandler


class _PausedDirectory:
    def __init__(self) -> None:
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.closed = False

    async def scandir(self, _path: str):
        attrs = asyncssh.SFTPAttrs(permissions=stat.S_IFREG | 0o644)
        try:
            for number in range(128):
                yield asyncssh.SFTPName(filename=f'a-{number:03d}', attrs=attrs)
            self.paused.set()
            await self.resume.wait()
            yield asyncssh.SFTPName(filename='z-target', attrs=attrs)
        finally:
            self.closed = True


class RemoteListingStreamTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def test_progress_is_published_before_directory_finishes(self) -> None:
        sftp = _PausedDirectory()
        snapshots: list[list[str]] = []
        task = asyncio.create_task(listdir(
            sftp, '/target', tab_id='test-tab',
            on_progress=lambda entries: snapshots.append([entry['name'] for entry in entries]),
        ))
        try:
            await asyncio.wait_for(sftp.paused.wait(), 1)
            self.assertFalse(task.done())
            self.assertEqual([len(snapshot) for snapshot in snapshots], [0, 1, 128])
            sftp.resume.set()
            entries = await task
            self.assertEqual(len(entries), 129)
            self.assertTrue(sftp.closed)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_closes_stream_and_discards_partial_cache(self) -> None:
        sftp = _PausedDirectory()
        manager = ConnectionManager(credential_store=Mock())
        manager._sessions['test-tab'] = SimpleNamespace(get_sftp=lambda: sftp)
        task = asyncio.create_task(manager.refresh_remote_list('test-tab', '/target'))
        try:
            await asyncio.wait_for(sftp.paused.wait(), 1)
            self.assertEqual(len(manager._remote_cache['test-tab']['/target']), 128)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(sftp.closed)
            self.assertNotIn('/target', manager._remote_cache['test-tab'])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_partial_ui_keeps_target_selection_until_later_batch(self) -> None:
        sftp = _PausedDirectory()
        manager = ConnectionManager(credential_store=Mock())
        manager._sessions['test-tab'] = SimpleNamespace(get_sftp=lambda: sftp)
        handler = SftpUiHandler('test-tab', manager, lambda: None)
        panel = RemoteFilePanel()
        panel.set_sftp_handler(handler)
        panel.table.set_list_callback(manager.get_remote_list_callback('test-tab'))
        manager.remote_list_progress.connect(lambda _tab, _path: panel.refresh(loading=True))
        manager.remote_list_updated.connect(lambda _tab, _path: panel.refresh())
        try:
            panel.table.set_directory('/target', select_name='z-target')
            await asyncio.wait_for(sftp.paused.wait(), 1)
            self.assertEqual(panel.table.rowCount(), 129)
            self.assertEqual(panel.table._pending_select_name, 'z-target')
            self.assertTrue(panel._loading)
            sftp.resume.set()
            await handler.wait_transfers_closed()
            selected = {panel.table.item(index.row(), 0).text() for index in panel.table.selectedIndexes()}
            self.assertEqual(selected, {'z-target'})
            self.assertFalse(panel._loading)
        finally:
            handler.cancel_transfers()
            await handler.wait_transfers_closed()
            panel.deleteLater()

    async def test_newer_refresh_keeps_cache_when_older_scan_finishes(self) -> None:
        old_scan, new_scan = _PausedDirectory(), _PausedDirectory()
        scans = iter((old_scan, new_scan))
        manager = ConnectionManager(credential_store=Mock())
        manager._sessions['test-tab'] = SimpleNamespace(get_sftp=lambda: next(scans))
        old_task = asyncio.create_task(manager.refresh_remote_list('test-tab', '/target'))
        new_task = None
        try:
            await asyncio.wait_for(old_scan.paused.wait(), 1)
            new_task = asyncio.create_task(manager.refresh_remote_list('test-tab', '/target'))
            await asyncio.wait_for(new_scan.paused.wait(), 1)
            current = manager._remote_cache['test-tab']['/target']
            old_scan.resume.set()
            await old_task
            self.assertIs(manager._remote_cache['test-tab']['/target'], current)
            new_scan.resume.set()
            await new_task
            self.assertEqual(len(manager._remote_cache['test-tab']['/target']), 129)
        finally:
            tasks = [old_task] + ([new_task] if new_task is not None else [])
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_directory_updates_grow_geometrically(self) -> None:
        class Directory:
            async def scandir(self, _path: str):
                attrs = asyncssh.SFTPAttrs(permissions=stat.S_IFREG | 0o644)
                for number in range(4096):
                    yield asyncssh.SFTPName(filename=str(number), attrs=attrs)

        counts: list[int] = []
        entries = await listdir(Directory(), '/target', tab_id='test-tab',
                                on_progress=lambda partial: counts.append(len(partial)))
        self.assertEqual(len(entries), 4096)
        self.assertEqual(counts, [0, 1, 128, 256, 512, 1024, 2048, 4096])

    async def test_failed_stream_clears_loading_without_automatic_retry(self) -> None:
        class FailedDirectory:
            async def scandir(self, _path: str):
                yield asyncssh.SFTPName(filename='partial', attrs=asyncssh.SFTPAttrs(
                    permissions=stat.S_IFREG | 0o644,
                ))
                raise RuntimeError('decode failed')

        manager = ConnectionManager(credential_store=Mock())
        manager._sessions['test-tab'] = SimpleNamespace(get_sftp=lambda: FailedDirectory())
        finished: list[str] = []
        manager.remote_list_updated.connect(lambda _tab, path: finished.append(path))
        with patch('core.connection_manager.logger.warning'):
            entries = await manager.refresh_remote_list('test-tab', '/target')
        self.assertEqual(entries, [])
        self.assertEqual(manager._remote_cache['test-tab']['/target'], [])
        self.assertEqual(finished, ['/target'])
        self.assertEqual(manager._remote_list_requests, {})
