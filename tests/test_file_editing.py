#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox, QSpinBox, QWidget

from ui.file_edit_manager import FileEditManager, _RemoteEditSession
from ui.file_panel.local_table import LocalFileTable
from ui.file_panel.remote_table import RemoteFileTable
from ui.settings_dialog import prompt_app_settings
from storage.app_config import _normalize_editor


class _FakeSftp:
    def __init__(self, *, size: int = 4, mtime: float = 10.0) -> None:
        self.attrs = SimpleNamespace(size=size, mtime=mtime, permissions=0o100644)

    async def stat(self, _path: str):
        return self.attrs


class _FakeConnectionManager:
    def __init__(self, sftp: _FakeSftp) -> None:
        self._ssh = SimpleNamespace(get_sftp=lambda: sftp)

    def get_session(self, _tab_id: str):
        return self._ssh

    def invalidate_remote_cache(self, _tab_id: str, _path: str) -> None:
        return None

    async def refresh_remote_list(self, _tab_id: str, _path: str):
        return []


class FileTableEditShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_f4_ignores_selected_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / 'sample.txt'
            folder_path = Path(directory) / 'folder'
            file_path.write_text('sample', encoding='utf-8')
            folder_path.mkdir()
            table = LocalFileTable(initial_path=directory)
            table.refresh()
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.text() in ('sample.txt', 'folder'):
                    item.setSelected(True)

            emitted = []
            table.edit_requested.connect(
                lambda paths, configured: emitted.append((paths, configured)),
            )
            table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F4, Qt.NoModifier))

            self.assertEqual(emitted, [([str(file_path)], True)])

    def test_remote_f4_emits_only_file_paths(self) -> None:
        table = RemoteFileTable()
        table.set_path('/tmp')
        table.set_list_callback(lambda _path: [
            {'name': 'folder', 'is_dir': True},
            {'name': 'remote.txt', 'is_dir': False, 'size': 4},
        ])
        table.refresh()
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.text() in ('folder', 'remote.txt'):
                item.setSelected(True)

        emitted = []
        table.edit_requested.connect(
            lambda paths, configured: emitted.append((paths, configured)),
        )
        table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F4, Qt.NoModifier))

        self.assertEqual(emitted, [(['/tmp/remote.txt'], True)])

    def test_settings_dialog_contains_editor_controls(self) -> None:
        captured = []

        def create_dialog(parent, title, *, min_width=400):
            dialog = QDialog(parent)
            dialog.setWindowTitle(title)
            dialog.setMinimumWidth(min_width)
            captured.append(dialog)
            QTimer.singleShot(0, dialog.reject)
            return dialog

        with patch('ui.settings_dialog.create_dialog', side_effect=create_dialog):
            result = prompt_app_settings(
                None,
                'dark',
                22,
                'Consolas',
                'en',
                r'C:\Tools\editor.exe',
                25,
            )

        self.assertIsNone(result)
        dialog = captured[0]
        editor_edit = dialog.findChild(QLineEdit, 'DefaultEditorPathEdit')
        size_spin = dialog.findChild(QSpinBox, 'RemoteEditLargeFileSpin')
        self.assertIsNotNone(editor_edit)
        self.assertIsNotNone(size_spin)
        self.assertEqual(editor_edit.text(), r'C:\Tools\editor.exe')
        self.assertEqual(size_spin.value(), 25)

    def test_editor_config_normalizes_path_and_threshold(self) -> None:
        normalized = _normalize_editor({
            'executable_path': '  C:/Tools/editor.exe  ',
            'remote_large_file_mb': 0,
        })
        self.assertEqual(normalized['executable_path'], 'C:/Tools/editor.exe')
        self.assertEqual(normalized['remote_large_file_mb'], 1)

    def test_remote_temp_name_is_portable_and_keeps_suffix(self) -> None:
        self.assertEqual(
            FileEditManager._safe_temp_name('report:2026?.tar.gz'),
            'report_2026_.tar.gz',
        )
        self.assertEqual(FileEditManager._safe_temp_name('CON.txt'), '_CON.txt')


class FileEditManagerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def asyncSetUp(self) -> None:
        self.sftp = _FakeSftp()
        self.parent = QWidget()
        self.manager = FileEditManager(_FakeConnectionManager(self.sftp), self.parent)

    async def asyncTearDown(self) -> None:
        await self.manager.close()
        self.parent.deleteLater()

    async def test_reopening_remote_file_reuses_download(self) -> None:
        async def fake_download(_sftp, _remote_path: str, local_path: str) -> None:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_text('data', encoding='utf-8')

        with (
            patch('ui.file_edit_manager.download', AsyncMock(side_effect=fake_download)) as mocked,
            patch.object(self.manager, '_launch_files') as launch,
        ):
            await self.manager._open_remote_files_async('tab-a', ['/tmp/a.txt'], True)
            await self.manager._open_remote_files_async('tab-a', ['/tmp/a.txt'], True)

        self.assertEqual(mocked.await_count, 1)
        self.assertEqual(launch.call_count, 2)
        first_path = launch.call_args_list[0].args[0][0]
        self.assertEqual(launch.call_args_list[1].args[0], [first_path])

    async def test_reopening_remote_file_refreshes_changed_remote_copy(self) -> None:
        contents = iter(('old1', 'new2'))

        async def fake_download(_sftp, _remote_path: str, local_path: str) -> None:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_text(next(contents), encoding='utf-8')

        with (
            patch('ui.file_edit_manager.download', AsyncMock(side_effect=fake_download)) as mocked,
            patch.object(self.manager, '_launch_files') as launch,
        ):
            await self.manager._open_remote_files_async('tab-a', ['/tmp/a.txt'], True)
            self.sftp.attrs = SimpleNamespace(
                size=4,
                mtime=20.0,
                permissions=0o100644,
            )
            await self.manager._open_remote_files_async('tab-a', ['/tmp/a.txt'], True)

        self.assertEqual(mocked.await_count, 2)
        local_path = Path(launch.call_args_list[1].args[0][0])
        self.assertEqual(local_path.read_text(encoding='utf-8'), 'new2')
        self.assertEqual(local_path.stat().st_mtime_ns, 20_000_000_000)
        session = self.manager._sessions[('tab-a', '/tmp/a.txt')]
        self.assertEqual(session.remote_signature, (4, 20_000_000_000))
        self.assertEqual(
            session.observed_local_signature,
            self.manager._local_signature(str(local_path)),
        )

    async def test_large_remote_file_requires_confirmation(self) -> None:
        self.sftp.attrs = SimpleNamespace(
            size=11 * 1024 * 1024,
            mtime=10.0,
            permissions=0o100644,
        )
        config = SimpleNamespace(
            editor=SimpleNamespace(remote_large_file_mb=10, executable_path=''),
        )
        with (
            patch('ui.file_edit_manager.get_app_config', return_value=config),
            patch('ui.file_edit_manager.ask_yes_no_async', AsyncMock(return_value=False)) as confirm,
            patch('ui.file_edit_manager.download', AsyncMock()) as download_file,
        ):
            await self.manager._open_remote_files_async('tab-a', ['/tmp/large.log'], False)

        confirm.assert_awaited_once()
        download_file.assert_not_awaited()

    async def test_remote_change_can_reload_and_skip_upload(self) -> None:
        local_path = Path(self.manager._temp_path('tab-a', '/tmp/a.txt'))
        local_path.write_text('local', encoding='utf-8')
        session = _RemoteEditSession(
            tab_id='tab-a',
            remote_path='/tmp/a.txt',
            local_path=str(local_path),
            remote_signature=(1, 1),
            observed_local_signature=self.manager._local_signature(str(local_path)),
        )
        self.sftp.attrs = SimpleNamespace(size=8, mtime=20.0, permissions=0o100644)

        async def fake_download(_sftp, _remote_path: str, target: str) -> None:
            Path(target).write_text('remote', encoding='utf-8')

        with (
            patch('ui.file_edit_manager.ask_yes_no_cancel_async', AsyncMock(return_value=QMessageBox.No)),
            patch('ui.file_edit_manager.download', AsyncMock(side_effect=fake_download)) as reload_file,
            patch('ui.file_edit_manager.upload', AsyncMock()) as upload_file,
        ):
            await self.manager._sync_session_async(
                session,
                session.observed_local_signature,
            )

        reload_file.assert_awaited_once()
        upload_file.assert_not_awaited()
        self.assertEqual(local_path.read_text(encoding='utf-8'), 'remote')

    async def test_local_change_prompts_once_for_observed_signature(self) -> None:
        local_path = Path(self.manager._temp_path('tab-a', '/tmp/watch.txt'))
        local_path.write_text('before', encoding='utf-8')
        session = _RemoteEditSession(
            tab_id='tab-a',
            remote_path='/tmp/watch.txt',
            local_path=str(local_path),
            remote_signature=(6, 1),
            observed_local_signature=self.manager._local_signature(str(local_path)),
        )
        self.manager._sessions[('tab-a', session.remote_path)] = session
        self.manager._sessions_by_local[os.path.normcase(str(local_path))] = session
        local_path.write_text('after-save', encoding='utf-8')
        self.manager._pending_local_paths.add(os.path.normcase(str(local_path)))

        with patch(
            'ui.file_edit_manager.ask_yes_no_async',
            AsyncMock(return_value=False),
        ) as prompt:
            self.manager._process_pending_changes()
            await asyncio.sleep(0.01)
            self.manager._pending_local_paths.add(os.path.normcase(str(local_path)))
            self.manager._process_pending_changes()
            await asyncio.sleep(0.01)

        prompt.assert_awaited_once()

    async def test_sync_task_is_reported_until_finished(self) -> None:
        local_path = Path(self.manager._temp_path('tab-a', '/tmp/sync.txt'))
        local_path.write_text('changed', encoding='utf-8')
        signature = self.manager._local_signature(str(local_path))
        session = _RemoteEditSession(
            tab_id='tab-a',
            remote_path='/tmp/sync.txt',
            local_path=str(local_path),
            remote_signature=(1, 1),
            observed_local_signature=signature,
        )
        blocker = asyncio.Event()

        async def wait_for_release(*_args) -> None:
            await blocker.wait()

        with (
            patch('ui.file_edit_manager.ask_yes_no_async', AsyncMock(return_value=True)),
            patch.object(
                self.manager,
                '_sync_session_async',
                AsyncMock(side_effect=wait_for_release),
            ),
        ):
            task = asyncio.create_task(self.manager._prompt_sync_async(session, signature))
            await asyncio.sleep(0.01)
            self.assertTrue(self.manager.has_running_syncs('tab-a'))
            blocker.set()
            await task

        self.assertFalse(self.manager.has_running_syncs('tab-a'))


if __name__ == '__main__':
    unittest.main()
