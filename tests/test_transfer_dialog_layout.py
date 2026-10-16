# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QHeaderView, QWidget

from core.transfer_queue import TransferJob
from storage import app_config
from storage.file_panel_defaults import DEFAULT_TRANSFER_COLUMN_WIDTHS, TRANSFER_TABLE_COLUMNS
from ui.sftp_ui_handler import SftpUiHandler
from ui.transfer_dialog import TransferDialog


class TransferDialogLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'config.json'
        raw = app_config._normalize_config(app_config._default_config())
        for name, value in (('_raw_config_cache', raw), ('_config_cache', app_config._to_app_config(raw))):
            patcher = patch.object(app_config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        save = app_config.save_transfer_dialog_layout
        patcher = patch('ui.transfer_dialog.save_transfer_dialog_layout', side_effect=lambda **kw: save(path=self.path, **kw))
        self.save = patcher.start()
        self.addCleanup(patcher.stop)
        self.handler = SftpUiHandler('layout-tab', object(), lambda: None)
        self.job = TransferJob('test-job', 'download', ('/source',), '/local', state='running', calculating=False, transferred=25, total=100, speed='1.1 MB/s')
        patcher = patch.object(self.handler, 'transfer_jobs', return_value=[self.job])
        self.jobs = patcher.start()
        self.addCleanup(patcher.stop)
        self.dialogs = []

    def tearDown(self) -> None:
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def dialog(self, kind='download', parent=None) -> TransferDialog:
        dialog = TransferDialog(self.handler, kind, parent)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    @staticmethod
    def widths(dialog: TransferDialog) -> list[int]:
        return [dialog._tree.columnWidth(column) for column in range(5)]

    def test_speed_and_window_resize_do_not_change_column_widths(self) -> None:
        dialog = self.dialog()
        header = dialog._tree.header()
        self.assertFalse(header.stretchLastSection())
        self.assertTrue(all(header.sectionResizeMode(column) == QHeaderView.Interactive for column in range(5)))
        widths = self.widths(dialog)
        for speed in ('234.2 KB/s', '123456.789 MB/s', '0 B/s'):
            self.jobs.return_value = [replace(self.job, speed=speed)]
            self.handler.transfer_jobs_changed.emit()
            self.app.processEvents()
            self.assertEqual(self.widths(dialog), widths)
            self.assertIn(speed, dialog._tree.topLevelItem(0).toolTip(3))
        self.save.assert_not_called()
        dialog.resize(1200, 500)
        self.app.processEvents()
        self.assertEqual(self.widths(dialog), widths)
        header.resizeSection(3, 333)
        self.handler.transfer_jobs_changed.emit()
        self.assertEqual(dialog._tree.columnWidth(3), 333)

    def test_escape_saves_to_disk_and_both_directions_restore_after_reload(self) -> None:
        dialog = self.dialog()
        dialog.resize(1100, 480)
        dialog._tree.header().resizeSection(0, 310)
        dialog._tree.header().resizeSection(3, 330)
        self.app.processEvents()
        expected_size = (dialog.width(), dialog.height())
        expected_widths = self.widths(dialog)
        finished = QSignalSpy(dialog.finished)
        QTest.keyClick(dialog, Qt.Key_Escape)
        # Esc 触发关闭按钮的快捷键，Qt 会先播放按钮点击动画。
        self.assertTrue(finished or finished.wait(500))
        data = json.loads(self.path.read_text(encoding='utf-8'))['file_panel']
        self.assertEqual((data['transfer_dialog_width'], data['transfer_dialog_height']), expected_size)
        self.assertEqual([data['transfer_column_widths'][name] for name in TRANSFER_TABLE_COLUMNS], expected_widths)
        app_config._config_cache = app_config._load_config(self.path)
        reopened = self.dialog('upload')
        self.assertEqual((reopened.width(), reopened.height()), expected_size)
        self.assertEqual(self.widths(reopened), expected_widths)

    def test_window_close_flushes_pending_layout(self) -> None:
        dialog = self.dialog()
        dialog.resize(1050, 450)
        dialog._tree.header().resizeSection(1, 255)
        self.app.processEvents()
        dialog.close()
        self.assertEqual(self.save.call_count, 1)
        saved = app_config._load_config(self.path).file_panel
        self.assertEqual(saved.transfer_column_widths['destination'], 255)
        self.assertEqual((saved.transfer_dialog_width, saved.transfer_dialog_height), (1050, 450))

    def test_parent_destruction_flushes_pending_snapshot(self) -> None:
        parent = QWidget()
        dialog = self.dialog(parent=parent)
        dialog.resize(1060, 460)
        dialog._tree.header().resizeSection(3, 350)
        self.app.processEvents()
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        saved = app_config._load_config(self.path).file_panel
        self.assertEqual(saved.transfer_column_widths['progress'], 350)
        self.assertEqual((saved.transfer_dialog_width, saved.transfer_dialog_height), (1060, 460))

    def test_unchanged_window_does_not_overwrite_another_windows_saved_layout(self) -> None:
        changed = self.dialog()
        unchanged = self.dialog('upload')
        changed.resize(1080, 470)
        changed._tree.header().resizeSection(3, 340)
        self.app.processEvents()
        changed.close()
        unchanged.close()
        self.assertEqual(self.save.call_count, 1)
        saved = app_config._load_config(self.path).file_panel
        self.assertEqual((saved.transfer_dialog_width, saved.transfer_dialog_height), (1080, 470))
        self.assertEqual(saved.transfer_column_widths['progress'], 340)

    def test_invalid_layout_values_are_normalized(self) -> None:
        normalized = app_config._normalize_file_panel({
            'transfer_dialog_width': -1, 'transfer_dialog_height': 99999,
            'transfer_column_widths': {'source': 'bad', 'progress': -5},
        })
        self.assertEqual(normalized['transfer_dialog_width'], 640)
        self.assertEqual(normalized['transfer_dialog_height'], 3000)
        self.assertEqual(normalized['transfer_column_widths']['source'], DEFAULT_TRANSFER_COLUMN_WIDTHS['source'])
        self.assertEqual(normalized['transfer_column_widths']['progress'], 40)
