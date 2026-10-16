# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication

from i18n import set_language, tr
from i18n.translator import Translator
from ui.file_panel.local_table import LocalFileTable
from ui.file_panel.panels import RemoteFilePanel
from ui.file_panel.widgets import _FilePanelStatusBar
from ui.sftp_ui_handler import SftpUiHandler


class TransferQueueTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def asyncSetUp(self) -> None:
        self.handler = SftpUiHandler('tab-a', object(), lambda: None)
        self.started: list[tuple[str, list[str], str]] = []
        self.entered = {name: asyncio.Event() for name in ('a', 'b', 'c', 'd')}
        self.release = {name: asyncio.Event() for name in self.entered}

        async def run(kind: str, paths: list[str], destination: str) -> None:
            self.started.append((kind, paths, destination))
            self.entered[paths[0]].set()
            await self.release[paths[0]].wait()

        async def upload(paths, dest):
            await run('upload', paths, dest)

        async def download(paths, dest):
            await run('download', paths, dest)

        self.upload_patch = patch.object(self.handler, '_upload_async', new=upload)
        self.download_patch = patch.object(self.handler, '_download_async', new=download)
        self.upload_patch.start()
        self.download_patch.start()

    async def asyncTearDown(self) -> None:
        self.handler.cancel_transfers()
        await self.handler.wait_transfers_closed()
        self.upload_patch.stop()
        self.download_patch.stop()

    async def entered_task(self, name: str) -> None:
        await asyncio.wait_for(self.entered[name].wait(), 1)

    async def test_fifo_per_direction_and_captured_upload_destination(self) -> None:
        self.handler.set_remote_dir('/original')
        paths = ['a']
        self.handler.upload_local_paths(paths)
        self.handler.upload_local_paths(['b'])
        paths.append('changed')
        self.handler.set_remote_dir('/later')
        await self.entered_task('a')
        self.assertEqual(self.started, [('upload', ['a'], '/original')])
        self.assertEqual([j.state for j in self.handler.transfer_jobs('upload')], ['running', 'queued'])
        self.release['a'].set()
        await self.entered_task('b')
        self.assertEqual(self.started[-1], ('upload', ['b'], '/original'))
        self.release['b'].set()
        await self.handler.wait_transfers_closed()
        self.assertEqual(self.handler.transfer_jobs('upload'), [])

    async def test_download_queue_and_upload_can_run_together(self) -> None:
        self.handler.set_local_dir('/first')
        self.handler.download_remote_paths(['a'])
        self.handler.download_remote_paths_to(['b'], '/chosen')
        self.handler.set_local_dir('/later')
        self.handler.upload_local_paths(['c'])
        await self.entered_task('a')
        await self.entered_task('c')
        self.assertFalse(self.entered['b'].is_set())
        self.release['a'].set()
        await self.entered_task('b')
        self.assertIn(('download', ['a'], '/first'), self.started)
        self.assertIn(('download', ['b'], '/chosen'), self.started)

    async def test_remove_waiting_job_and_keep_fifo(self) -> None:
        for name in ('a', 'b', 'c'):
            self.handler.upload_local_paths([name])
        jobs = self.handler.transfer_jobs('upload')
        self.handler.cancel_transfer(jobs[1].id)
        self.handler.cancel_transfer(jobs[1].id)
        await self.entered_task('a')
        self.release['a'].set()
        await self.entered_task('c')
        self.assertFalse(self.entered['b'].is_set())

    async def test_stale_queued_id_cancels_just_started_task_and_waits_for_cleanup(self) -> None:
        cancelling = asyncio.Event()
        cleanup = asyncio.Event()

        async def run(paths, _destination):
            name = paths[0]
            self.entered[name].set()
            try:
                await self.release[name].wait()
            finally:
                if name == 'b':
                    cancelling.set()
                    await cleanup.wait()

        with patch.object(self.handler, '_download_async', new=run):
            for name in ('a', 'b', 'c'):
                self.handler.download_remote_paths_to([name], '/target')
            stale_id = self.handler.transfer_jobs('download')[1].id
            await self.entered_task('a')
            self.release['a'].set()
            await self.entered_task('b')
            self.handler.cancel_transfer(stale_id)
            self.handler.cancel_transfer(stale_id)
            await asyncio.wait_for(cancelling.wait(), 1)
            self.assertEqual(self.handler.transfer_jobs('download')[0].state, 'cancelling')
            self.assertFalse(self.entered['c'].is_set())
            cleanup.set()
            await self.entered_task('c')
            self.handler.cancel_transfer(stale_id)
            self.assertEqual(self.handler.transfer_jobs('download')[0].paths, ('c',))

    async def test_cancel_all_during_individual_cancellation_preserves_cleanup(self) -> None:
        cancelling = asyncio.Event()
        cleanup = asyncio.Event()
        cleaned = asyncio.Event()

        async def run(_paths, _destination):
            self.entered['a'].set()
            try:
                await self.release['a'].wait()
            finally:
                cancelling.set()
                await cleanup.wait()
                cleaned.set()

        with patch.object(self.handler, '_upload_async', new=run):
            self.handler.upload_local_paths(['a'])
            self.handler.upload_local_paths(['b'])
            await self.entered_task('a')
            self.handler.cancel_transfer(self.handler.transfer_jobs('upload')[0].id)
            await asyncio.wait_for(cancelling.wait(), 1)
            self.handler.cancel_transfers()
            self.handler.cancel_transfers()
            cleanup.set()
            await self.handler.wait_transfers_closed()
            self.assertTrue(cleaned.is_set())
            self.assertFalse(self.entered['b'].is_set())

    async def test_cancel_before_coroutine_first_step_advances_queue(self) -> None:
        self.handler.upload_local_paths(['a'])
        self.handler.upload_local_paths(['b'])
        self.handler.cancel_transfer(self.handler.transfer_jobs('upload')[0].id)
        await self.entered_task('b')
        self.assertFalse(self.entered['a'].is_set())

    async def test_cancel_all_does_not_start_queued_jobs(self) -> None:
        self.handler.upload_local_paths(['a'])
        self.handler.upload_local_paths(['b'])
        self.handler.download_remote_paths_to(['c'], '/target')
        self.handler.download_remote_paths_to(['d'], '/target')
        await self.entered_task('a')
        await self.entered_task('c')
        self.handler.cancel_transfers()
        await self.handler.wait_transfers_closed()
        self.assertFalse(self.entered['b'].is_set())
        self.assertFalse(self.entered['d'].is_set())
        self.assertEqual(self.handler.transfer_jobs('upload'), [])
        self.assertEqual(self.handler.transfer_jobs('download'), [])
        self.assertFalse(self.handler.has_running_transfers())

    async def test_failure_advances_queue(self) -> None:
        async def run(paths, _destination):
            if paths == ['a']:
                raise OSError('test failure')
            self.entered['b'].set()

        with patch.object(self.handler, '_upload_async', new=run):
            self.handler.upload_local_paths(['a'])
            self.handler.upload_local_paths(['b'])
            await self.entered_task('b')
            await self.handler.wait_transfers_closed()
        self.assertEqual(self.handler.transfer_jobs('upload'), [])

    async def test_arrow_opens_one_non_modal_list_and_buttons_follow_job_ids(self) -> None:
        table = LocalFileTable(initial_path=os.getcwd())
        bar = _FilePanelStatusBar(table, transfer_kind='upload')
        bar.set_sftp_handler(self.handler)
        try:
            self.handler.upload_local_paths(['a'])
            self.handler.upload_local_paths(['b'])
            self.handler.upload_local_paths(['c'])
            self.assertTrue(bar._transfer_button.isEnabled())
            self.assertFalse(bar._transfer_button.isHidden())
            self.assertFalse(bar._transfer_button.icon().isNull())
            self.assertEqual(bar._transfer_button.text(), '3')
            self.assertEqual(bar._transfer_button.toolTip(), tr('file.transfers.upload_tooltip', count=3, queued=2))
            bar._transfer_button.click()
            dialog = bar._transfer_dialog
            self.assertIsNotNone(dialog)
            self.assertFalse(dialog.isModal())
            self.assertEqual(dialog._tree.topLevelItemCount(), 3)
            bar._transfer_button.click()
            self.assertIs(bar._transfer_dialog, dialog)
            jobs = self.handler.transfer_jobs('upload')
            stale_button = dialog._rows[jobs[1].id][1]
            dialog._rows[jobs[2].id][1].click()
            self.assertEqual(dialog._tree.topLevelItemCount(), 2)
            await self.entered_task('a')
            self.release['a'].set()
            await self.entered_task('b')
            self.assertIs(dialog._rows[jobs[1].id][1], stale_button)
            stale_button.click()
            self.assertFalse(stale_button.isEnabled())
            await self.handler.wait_transfers_closed()
            self.assertEqual(dialog._tree.topLevelItemCount(), 0)
            self.assertTrue(bar._transfer_button.isHidden())
            dialog.close()
            self.assertIsNone(bar._transfer_dialog)
        finally:
            bar.set_sftp_handler(None)
            bar.deleteLater()
            table.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    async def test_progress_and_language_update_in_open_dialog(self) -> None:
        from ui.transfer_dialog import TransferDialog

        self.handler.upload_local_paths(['a'])
        # 隔离翻译回调，避免其它 GUI 测试遗留窗口影响此用例。
        with patch('i18n.translator._translator', Translator()):
            dialog = TransferDialog(self.handler, 'upload')
            table = LocalFileTable(initial_path=os.getcwd())
            bar = _FilePanelStatusBar(table, transfer_kind='upload')
            bar.set_sftp_handler(self.handler)
            try:
                self.handler._begin_transfer_status('upload', 5_000_000_000)
                self.handler._make_progress_handler('upload')('a', '/a', 1_000_000_000, 5_000_000_000)
                job = self.handler.transfer_jobs('upload')[0]
                self.assertEqual((job.transferred, job.total), (1_000_000_000, 5_000_000_000))
                item, _button = dialog._rows[job.id]
                self.assertIn('20.0%', item.text(3))
                set_language('zh-CN')
                self.assertEqual(dialog.windowTitle(), '上传任务')
                self.assertEqual(item.text(2), tr('file.transfers.running'))
                self.assertEqual(bar._transfer_button.text(), '1')
                self.assertEqual(bar._transfer_button.accessibleName(), '上传任务')
            finally:
                bar.set_sftp_handler(None)
                bar.deleteLater()
                table.deleteLater()
                dialog.close()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    async def test_tabs_have_independent_queues(self) -> None:
        other = SftpUiHandler('tab-b', object(), lambda: None)
        other_entered = asyncio.Event()

        async def run(_paths, _destination):
            other_entered.set()
            await self.release['c'].wait()

        with patch.object(other, '_upload_async', new=run):
            try:
                self.handler.upload_local_paths(['a'])
                self.handler.upload_local_paths(['b'])
                other.upload_local_paths(['c'])
                await self.entered_task('a')
                await asyncio.wait_for(other_entered.wait(), 1)
                other.cancel_transfers()
                await other.wait_transfers_closed()
                self.assertEqual(len(self.handler.transfer_jobs('upload')), 2)
                self.assertTrue(self.handler.has_running_transfers())
            finally:
                other.cancel_transfers()
                await other.wait_transfers_closed()


class SftpQueueIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def test_directory_scan_and_active_progress_are_unchanged_by_queued_file(self) -> None:
        handler = SftpUiHandler(
            'tab-a', SimpleNamespace(get_session=lambda _tab: SimpleNamespace(get_sftp=lambda: object())),
            lambda: None,
        )
        panel = RemoteFilePanel()
        panel.set_sftp_handler(handler)
        bar = panel.statusbar
        scan_started, finish_scan, transfer_started, finish_folder, file_started, finish_file = [
            asyncio.Event() for _ in range(6)
        ]
        calls = []

        async def size(_sftp, path, _visited):
            if path == '/folder':
                scan_started.set()
                await finish_scan.wait()
                return 1000
            return 100

        async def download(_sftp, source, target, progress, *_args, **_kwargs):
            calls.append(source)
            if source == '/folder':
                progress(source, target, 250, 1000)
                transfer_started.set()
                await finish_folder.wait()
            else:
                progress(source, target, 10, 100)
                file_started.set()
                await finish_file.wait()

        with (
            patch.object(handler, '_remote_path_size', side_effect=size),
            patch('ui.sftp_ui_handler.download', side_effect=download),
        ):
            try:
                handler.download_remote_paths_to(['/folder'], '/local')
                await asyncio.wait_for(scan_started.wait(), 1)
                self.assertTrue(bar._transfer_calculating)
                self.assertTrue(getattr(bar._progress_bar, '_indeterminate', False))
                bar._progress_bar._advance_busy_progress()
                phase = bar._progress_bar._busy_phase
                folder_id = bar._active_transfer_job_id
                handler.download_remote_paths_to(['/file'], '/local')
                self.assertEqual([job.state for job in handler.transfer_jobs('download')], ['running', 'queued'])
                self.assertTrue(bar._transfer_calculating)
                self.assertTrue(bar._progress_bar._indeterminate)
                self.assertEqual(bar._progress_bar._busy_phase, phase)
                self.assertEqual(bar._active_transfer_job_id, folder_id)
                self.assertEqual(calls, [])
                finish_scan.set()
                await asyncio.wait_for(transfer_started.wait(), 1)
                self.assertFalse(bar._transfer_calculating)
                self.assertFalse(bar._progress_bar._indeterminate)
                self.assertEqual(bar._transfer_progress, 0.25)
                queued = handler.transfer_jobs('download')[1]
                handler.cancel_transfer(queued.id)
                self.assertEqual(bar._transfer_progress, 0.25)
                self.assertEqual(calls, ['/folder'])
                handler.download_remote_paths_to(['/file'], '/local')
                self.assertEqual(bar._transfer_progress, 0.25)
                finish_folder.set()
                await asyncio.wait_for(file_started.wait(), 1)
                self.assertNotEqual(bar._active_transfer_job_id, folder_id)
                self.assertEqual(bar._transfer_progress, 0.10)
                self.assertEqual(calls, ['/folder', '/file'])
                finish_file.set()
                await handler.wait_transfers_closed()
                self.assertTrue(bar._progress_bar.isHidden())
                self.assertTrue(bar._transfer_button.isHidden())
            finally:
                handler.cancel_transfers()
                await handler.wait_transfers_closed()
                panel.set_sftp_handler(None)
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    async def test_binding_panel_during_transfer_restores_current_job_progress(self) -> None:
        handler = SftpUiHandler('tab-a', object(), lambda: None)
        started, release = asyncio.Event(), asyncio.Event()

        async def run(_paths, _destination):
            handler._begin_transfer_status('download', 1000)
            handler._make_progress_handler('download')('/folder/file', '/local/file', 250, 1000)
            started.set()
            await release.wait()

        with patch.object(handler, '_download_async', new=run):
            panel = RemoteFilePanel()
            try:
                handler.download_remote_paths_to(['/folder'], '/local')
                await asyncio.wait_for(started.wait(), 1)
                panel.set_sftp_handler(handler)
                self.assertEqual(panel.statusbar._transfer_progress, 0.25)
                handler.download_remote_paths_to(['/file'], '/local')
                self.assertEqual(panel.statusbar._transfer_progress, 0.25)
            finally:
                handler.cancel_transfers()
                await handler.wait_transfers_closed()
                panel.set_sftp_handler(None)
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    async def test_real_upload_runner_keeps_destination_while_waiting(self) -> None:
        cm = SimpleNamespace(
            get_session=lambda _tab: SimpleNamespace(get_sftp=lambda: object()),
            invalidate_remote_cache=Mock(), refresh_remote_list=AsyncMock(),
        )
        handler = SftpUiHandler('tab-a', cm, lambda: None)
        first_started = asyncio.Event()
        release = asyncio.Event()
        destinations = []

        async def upload(_sftp, source, destination, *_args, **_kwargs):
            destinations.append(destination)
            if source == 'a':
                first_started.set()
                await release.wait()

        with (
            patch('ui.sftp_ui_handler.upload', side_effect=upload),
            patch('ui.sftp_ui_handler.asyncio.to_thread', new=AsyncMock(return_value=100)),
        ):
            try:
                handler.set_remote_dir('/original')
                handler.upload_local_paths(['a'])
                await asyncio.wait_for(first_started.wait(), 1)
                handler.upload_local_paths(['b'])
                handler.set_remote_dir('/later')
                release.set()
                await handler.wait_transfers_closed()
                self.assertEqual(destinations, ['/original/a', '/original/b'])
            finally:
                handler.cancel_transfers()
                await handler.wait_transfers_closed()
