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
from core.sftp_diagnostics import SftpTiming
from core.sftp_service import listdir
from models.favorite_path import FavoritePath
from ui.file_panel.panels import RemoteFilePanel
from ui.sftp_ui_handler import SftpUiHandler


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _SlowDirectory:
    async def scandir(self, _path: str):
        await asyncio.sleep(0.55)
        for name in ['.', '..', 'a', 'b', 'c', 'd', 'link']:
            yield asyncssh.SFTPName(filename=name, attrs=asyncssh.SFTPAttrs(
                permissions=(stat.S_IFLNK if name == 'link' else stat.S_IFREG) | 0o644,
                size=7, mtime=1,
            ))

    async def lstat(self, _path: str) -> asyncssh.SFTPAttrs:
        raise AssertionError('批量列表不应再发送 LSTAT')

    async def stat(self, _path: str) -> asyncssh.SFTPAttrs:
        return asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755)


class RemoteNavigationTimingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def test_slow_listing_reports_batch_read_and_symlink_cost(self) -> None:
        with patch('core.sftp_service.logger.warning') as warning:
            entries = await listdir(_SlowDirectory(), '/target', tab_id='test-tab')

        self.assertEqual(len(entries), 5)
        self.assertTrue(entries[-1]['is_dir'])
        messages = [call.args[0] for call in warning.call_args_list]
        summaries = [message for message in messages if 'status=done' in message]
        self.assertTrue(summaries, '超过 500ms 的真实列表调用必须输出耗时汇总')
        summary = summaries[-1]
        for field in ('[SFTP-SLOW]', 'tab_id=test-tab', "path='/target'",
                      'lstat_count=0', 'read_next_count=8', 'first_entry_ms=',
                      'symlink_stat_count=1', 'entries=5'):
            self.assertIn(field, summary)
        self.assertTrue(any('status=waiting' in message for message in messages))

    async def test_threshold_is_strict_and_fast_operation_cancels_watchdog(self) -> None:
        clock = _Clock()
        with (
            patch('core.sftp_diagnostics.time.perf_counter', clock),
            patch('core.sftp_diagnostics.logger.warning') as warning,
        ):
            with SftpTiming('test', 'test-tab', '/') as timing:
                clock.now = 0.5
            warning.assert_not_called()
            self.assertTrue(timing._watchdog.cancelled())

    async def test_waiting_log_restores_sftp_phase_after_ui_progress_finishes(self) -> None:
        clock = _Clock()
        with (
            patch('core.sftp_diagnostics.time.perf_counter', clock),
            patch('core.sftp_diagnostics.logger.warning') as warning,
        ):
            with SftpTiming('refresh_remote_list', 'test-tab', '/target') as timing:
                with timing.measure('sftp_list'):
                    with timing.measure('ui_progress'):
                        clock.now = 0.002
                    clock.now = 0.51
                    timing._report('waiting')
                    message = warning.call_args.args[0]
                    self.assertIn('phase=sftp_list,', message)
                    self.assertIn('phase_elapsed_ms=510.0', message)
                    self.assertIn('ui_progress_ms=2.0', message)

    async def test_cancelled_slow_operation_reports_and_stops_watchdog(self) -> None:
        clock = _Clock()
        with (
            patch('core.sftp_diagnostics.time.perf_counter', clock),
            patch('core.sftp_diagnostics.logger.warning') as warning,
        ):
            with self.assertRaises(asyncio.CancelledError):
                with SftpTiming('test', 'test-tab', '/') as timing:
                    with timing.measure('names'):
                        clock.now = 0.6
                        raise asyncio.CancelledError
            self.assertIn('status=cancelled', warning.call_args.args[0])
            self.assertTrue(timing._watchdog.cancelled())

    async def test_early_qt_timer_rechecks_threshold_before_logging(self) -> None:
        clock = _Clock()
        fake_loop = Mock()
        with (
            patch('core.sftp_diagnostics.time.perf_counter', clock),
            patch('core.sftp_diagnostics.asyncio.get_running_loop', return_value=fake_loop),
            patch('core.sftp_diagnostics.logger.warning') as warning,
        ):
            timing = SftpTiming('test', 'test-tab', '/')
            clock.now = 0.495
            timing._report_waiting()
            warning.assert_not_called()
            self.assertAlmostEqual(fake_loop.call_later.call_args.args[0], 0.005)
            clock.now = 0.501
            timing._report_waiting()
            self.assertIn('status=waiting', warning.call_args.args[0])

    async def test_favorite_reports_total_time_when_individual_stages_are_fast(self) -> None:
        clock = _Clock()
        listed_paths: list[str] = []

        class Directory:
            async def lstat(self, path: str) -> asyncssh.SFTPAttrs:
                if path == '/target':
                    clock.now += 0.3
                    return asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755)
                return asyncssh.SFTPAttrs(permissions=stat.S_IFREG | 0o644)

            async def scandir(self, path: str):
                listed_paths.append(path)
                if path == '/target':
                    clock.now += 0.31
                    yield asyncssh.SFTPName(filename='child', attrs=asyncssh.SFTPAttrs(
                        permissions=stat.S_IFREG | 0o644,
                    ))

        sftp = Directory()
        manager = ConnectionManager(credential_store=Mock())
        manager._sessions['test-tab'] = SimpleNamespace(get_sftp=lambda: sftp)
        handler = SftpUiHandler('test-tab', manager, lambda: None)
        panel = RemoteFilePanel()
        panel.set_sftp_handler(handler)
        panel.table.set_list_callback(manager.get_remote_list_callback('test-tab'))

        def update(_tab_id: str, path: str) -> None:
            if panel.current_path() == path:
                panel.refresh()

        manager.remote_list_updated.connect(update)
        try:
            with (
                patch('core.sftp_diagnostics.time.perf_counter', clock),
                patch('core.sftp_diagnostics.logger.warning') as warning,
            ):
                panel._navigate_favorite(FavoritePath(path='/target', is_file=False))
                await asyncio.gather(*panel._background_tasks)
                await asyncio.gather(*handler._background_tasks, *manager._refresh_tasks.values())
            self.assertEqual(panel.current_path(), '/target')
            self.assertEqual(panel.table.rowCount(), 2)
            self.assertEqual(listed_paths, ['/target'], '已解析为目录的收藏不应再加载父目录')
            messages = [call.args[0] for call in warning.call_args_list]
            summary = next(message for message in messages
                           if 'operation=refresh_remote_list' in message and "path='/target'" in message)
            for field in ('source=favorite', 'request_id=', "requested_path='/target'",
                          'elapsed_ms=310.0', 'request_elapsed_ms=610.0',
                          'ui_update_count=1', 'entries=1'):
                self.assertIn(field, summary)
            request_ids = {message.split('request_id=')[1].split(',')[0] for message in messages}
            self.assertEqual(len(request_ids), 1)
        finally:
            handler.cancel_transfers()
            await handler.wait_transfers_closed()
            panel.deleteLater()

    async def test_slow_path_resolution_records_parent_fallbacks(self) -> None:
        clock = _Clock()

        class MissingPath:
            async def lstat(self, path: str) -> asyncssh.SFTPAttrs:
                clock.now += 0.2
                if path != '/':
                    raise asyncssh.SFTPNoSuchFile('missing')
                return asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755)

        handler = SftpUiHandler('test-tab', Mock(), lambda: None)
        handler._cm.get_session.return_value = SimpleNamespace(get_sftp=lambda: MissingPath())
        with (
            patch('core.sftp_diagnostics.time.perf_counter', clock),
            patch('core.sftp_diagnostics.logger.warning') as warning,
        ):
            result = await handler.resolve_remote_navigation_target('/missing/child')
        self.assertEqual(result, ('/', '', None))
        summary = warning.call_args.args[0]
        self.assertIn('parent_fallbacks=2', summary)
        self.assertIn("last_probe_error='SFTPNoSuchFile'", summary)
        self.assertIn('lstat_count=3', summary)


if __name__ == '__main__':
    unittest.main()
