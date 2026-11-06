#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from storage import app_config
from ui.favorites_dialog import LocalFavoritesDialog
from ui.file_dialog_history import initial_directory, remember_selection
from ui.file_panel.remote_table import RemoteFileTable
from ui.session_dialog import SessionDialog


class FileDialogHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config_path = self.root / 'config.json'
        raw = app_config._default_config()
        for name, value in (
            ('_raw_config_cache', raw),
            ('_config_cache', app_config._to_app_config(raw)),
        ):
            patcher = patch.object(app_config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(
            'ui.file_dialog_history.save_file_dialog_directory',
            partial(app_config.save_file_dialog_directory, path=self.config_path),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def directory(self, name: str) -> str:
        path = self.root / name
        path.mkdir()
        return str(path)

    def test_session_browsers_keep_separate_directories_after_reload(self) -> None:
        key_dir = self.directory('keys')
        config_dir = self.directory('ssh')
        local_dir = self.directory('local')
        dialog = SessionDialog()
        self.addCleanup(dialog.deleteLater)
        with patch('ui.session_dialog.QFileDialog.getOpenFileName') as choose_file:
            choose_file.return_value = (str(Path(key_dir) / 'id_ed25519'), '')
            dialog._browse_key()
            choose_file.return_value = (str(Path(config_dir) / 'config'), '')
            dialog._browse_ssh_config()
        with patch('ui.session_dialog.QFileDialog.getExistingDirectory', return_value=local_dir):
            dialog._browse_local()

        app_config._config_cache = app_config._load_config(self.config_path)
        reopened = SessionDialog()
        self.addCleanup(reopened.deleteLater)
        before_cancel = self.config_path.read_bytes()
        with patch('ui.session_dialog.QFileDialog.getOpenFileName', return_value=('', '')) as choose:
            reopened._browse_key()
            self.assertEqual(choose.call_args.args[2], key_dir)
            reopened._browse_ssh_config()
            self.assertEqual(choose.call_args.args[2], config_dir)
        with patch('ui.session_dialog.QFileDialog.getExistingDirectory', return_value='') as choose:
            reopened._browse_local()
            self.assertEqual(choose.call_args.args[2], local_dir)
        self.assertEqual(self.config_path.read_bytes(), before_cancel)

    def test_download_remembers_target_across_tables_and_keeps_signal(self) -> None:
        target = self.directory('downloads')
        table = RemoteFileTable()
        self.addCleanup(table.deleteLater)
        table._current_path = '/remote'
        received = Mock()
        table.download_to_requested.connect(received)
        with patch('ui.file_panel.remote_table.QFileDialog.getExistingDirectory', return_value=target):
            table._download_selected_to_other([('report.txt', 'file')])
        received.assert_called_once_with(['/remote/report.txt'], target)

        other = RemoteFileTable()
        self.addCleanup(other.deleteLater)
        cancelled = Mock()
        other.download_to_requested.connect(cancelled)
        with patch('ui.file_panel.remote_table.QFileDialog.getExistingDirectory', return_value='') as choose:
            other._download_selected_to_other([('other.txt', 'file')])
            self.assertEqual(choose.call_args.args[2], target)
        cancelled.assert_not_called()
        self.assertEqual(initial_directory('session.local_directory', 'fallback'), 'fallback')

    def test_global_and_session_favorites_have_independent_browse_history(self) -> None:
        global_dir = self.directory('global')
        session_dir = self.directory('session')
        dialog = LocalFavoritesDialog(global_entries=[], session_entries=[])
        self.addCleanup(dialog.deleteLater)
        with patch('ui.favorites_dialog.QFileDialog.getExistingDirectory') as choose:
            choose.return_value = global_dir
            dialog._global_editor._browse_and_add()
            choose.return_value = session_dir
            dialog._session_editor._browse_and_add()
            choose.return_value = ''
            dialog._global_editor._browse_and_add()
            self.assertEqual(choose.call_args.args[2], global_dir)
            dialog._session_editor._browse_and_add()
            self.assertEqual(choose.call_args.args[2], session_dir)

    def test_missing_directory_uses_original_fallback(self) -> None:
        directory = self.directory('removed')
        remember_selection('settings.editor', str(Path(directory) / 'editor.exe'), is_file=True)
        self.assertEqual(initial_directory('settings.editor', 'fallback'), directory)
        Path(directory).rmdir()
        self.assertEqual(initial_directory('settings.editor', 'fallback'), 'fallback')

    def test_failed_save_keeps_previous_history_and_does_not_raise(self) -> None:
        previous = self.directory('previous')
        remember_selection('remote.download', previous)
        saved = self.config_path.read_bytes()
        with patch('storage.app_config._save_config', side_effect=OSError('full')):
            remember_selection('remote.download', self.directory('new'))
        self.assertEqual(initial_directory('remote.download'), previous)
        self.assertEqual(self.config_path.read_bytes(), saved)


if __name__ == '__main__':
    unittest.main()
