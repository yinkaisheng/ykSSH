#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QMessageBox, QSpinBox, QWidget

from ui.file_edit_manager import FileEditManager, _RemoteEditSession
from ui.file_panel.local_table import LocalFileTable
from ui.file_panel.panels import FilesPanel, LocalFilePanel, RemoteFilePanel
from ui.file_panel.remote_table import RemoteFileTable
from ui.file_panel.widgets import _FilePanelStatusBar
from ui.sftp_ui_handler import SftpUiHandler
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

    def test_f3_uses_system_associated_application(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / 'sample.wav'
            file_path.write_bytes(b'RIFF')
            table = LocalFileTable(initial_path=directory)
            table.refresh()
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.text() == 'sample.wav':
                    table.setCurrentItem(item)
                    item.setSelected(True)

            emitted = []
            table.edit_requested.connect(
                lambda paths, configured: emitted.append((paths, configured)),
            )
            table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F3, Qt.NoModifier))

            self.assertEqual(emitted, [([str(file_path)], False)])

    def test_escape_from_rename_restores_table_focus_for_next_f2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / 'sample.py'
            file_path.write_text('sample', encoding='utf-8')
            table = LocalFileTable(initial_path=directory)
            table.refresh()
            table.show()
            table.activateWindow()
            table.setFocus(Qt.OtherFocusReason)
            self.app.processEvents()
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.text() == 'sample.py':
                    table.setCurrentItem(item)
                    item.setSelected(True)

            table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier))
            first_edit = table._inline_rename_edit
            self.assertIsNotNone(first_edit)
            first_edit.keyPressEvent(
                QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
            )
            self.app.processEvents()

            self.assertIsNone(table._inline_rename_edit)
            self.assertTrue(table.hasFocus())

            table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F2, Qt.NoModifier))
            self.assertIsNotNone(table._inline_rename_edit)
            self.assertIsNot(table._inline_rename_edit, first_edit)

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

    def test_remote_f3_uses_system_associated_application(self) -> None:
        table = RemoteFileTable()
        table.set_path('/tmp')
        table.set_list_callback(lambda _path: [
            {'name': 'remote.wav', 'is_dir': False, 'size': 4},
        ])
        table.refresh()
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.text() == 'remote.wav':
                table.setCurrentItem(item)
                item.setSelected(True)

        emitted = []
        table.edit_requested.connect(
            lambda paths, configured: emitted.append((paths, configured)),
        )
        table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F3, Qt.NoModifier))

        self.assertEqual(emitted, [(['/tmp/remote.wav'], False)])

    def test_enter_opens_selected_files_with_system_association(self) -> None:
        table = RemoteFileTable()
        table.set_path('/tmp')
        table.set_list_callback(lambda _path: [
            {'name': 'folder', 'is_dir': True},
            {'name': 'a.wav', 'is_dir': False, 'size': 4},
            {'name': 'b.txt', 'is_dir': False, 'size': 3},
        ])
        table.refresh()
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.text() in ('folder', 'a.wav', 'b.txt'):
                item.setSelected(True)

        emitted = []
        table.edit_requested.connect(
            lambda paths, configured: emitted.append((paths, configured)),
        )
        table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))

        self.assertEqual(emitted, [(['/tmp/a.wav', '/tmp/b.txt'], False)])

    def test_arrow_keys_navigate_remote_directories(self) -> None:
        table = RemoteFileTable()
        table.set_path('/tmp')
        table.set_list_callback(lambda _path: [
            {'name': 'folder', 'is_dir': True},
        ])
        table.refresh()
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.text() == 'folder':
                table.setCurrentItem(item)
                item.setSelected(True)

        table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
        self.assertEqual(table.current_path(), '/tmp/folder')

        table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier))
        self.assertEqual(table.current_path(), '/tmp')

        table.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.ControlModifier)
        )
        self.assertEqual(table.current_path(), '/')

    def test_alt_enter_opens_properties_for_selection(self) -> None:
        table = RemoteFileTable()
        table.set_path('/tmp')
        table.set_list_callback(lambda _path: [
            {'name': 'a.txt', 'is_dir': False, 'size': 3},
        ])
        table.refresh()
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None and item.text() == 'a.txt':
                table.setCurrentItem(item)
                item.setSelected(True)
        properties = Mock()
        table._properties = properties

        table.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier)
        )

        properties.assert_called_once_with()

    def test_file_panel_focus_shortcuts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = FilesPanel(initial_local_path=directory)
            panel.show()
            panel.activateWindow()
            remote_table = panel.remote_file_panel.table
            remote_table.show()
            local_table = panel.local_file_panel.table
            local_table.setFocus(Qt.OtherFocusReason)
            self.app.processEvents()
            self.assertTrue(local_table.property('panelFocused'))
            self.assertFalse(remote_table.property('panelFocused'))

            local_table.keyPressEvent(
                QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.ControlModifier)
            )
            self.app.processEvents()
            local_path_edit = panel.local_file_panel.path_edit
            self.assertTrue(local_path_edit.hasFocus())
            self.assertEqual(local_path_edit.selectedText(), local_path_edit.text())

            local_path_edit.keyPressEvent(
                QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.ControlModifier)
            )
            self.assertTrue(local_table.hasFocus())

            local_table.keyPressEvent(
                QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.AltModifier)
            )
            self.app.processEvents()
            self.assertTrue(remote_table.hasFocus())
            self.assertFalse(local_table.property('panelFocused'))
            self.assertTrue(remote_table.property('panelFocused'))

            remote_table.keyPressEvent(
                QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.AltModifier)
            )
            self.app.processEvents()
            self.assertTrue(local_table.hasFocus())
            self.assertTrue(local_table.property('panelFocused'))
            self.assertFalse(remote_table.property('panelFocused'))

            panel.hide()
            panel.deleteLater()
            self.app.processEvents()

    def test_ctrl_n_triggers_new_folder_in_local_and_remote_tables(self) -> None:
        local_table = LocalFileTable(initial_path=str(Path.cwd()))
        remote_table = RemoteFileTable()
        local_mkdir = Mock()
        remote_mkdir = Mock()
        local_table._mkdir = local_mkdir
        remote_table._mkdir = remote_mkdir
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_N, Qt.ControlModifier)

        local_table.keyPressEvent(event)
        remote_table.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_N, Qt.ControlModifier))

        local_mkdir.assert_called_once_with()
        remote_mkdir.assert_called_once_with()

    def test_ctrl_r_refreshes_local_and_requests_remote_refresh(self) -> None:
        local_table = LocalFileTable(initial_path=str(Path.cwd()))
        remote_table = RemoteFileTable()
        local_refresh = Mock()
        remote_refresh = Mock()
        local_table.refresh = local_refresh
        remote_table.refresh_requested.connect(remote_refresh)

        local_table.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.ControlModifier)
        )
        remote_table.keyPressEvent(
            QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.ControlModifier)
        )

        local_refresh.assert_called_once_with()
        remote_refresh.assert_called_once_with()

    def test_toolbar_button_returns_focus_to_own_file_table(self) -> None:
        local_panel = LocalFilePanel(initial_path=str(Path.cwd()))
        remote_panel = RemoteFilePanel()
        remote_panel.set_list_callback(lambda _path: [])
        host = QWidget()
        local_panel.setParent(host)
        remote_panel.setParent(host)
        host.show()
        local_panel.show()
        remote_panel.show()
        self.app.processEvents()

        for panel in (local_panel, remote_panel):
            panel.path_edit.setFocus()
            self.assertTrue(panel.path_edit.hasFocus())
            panel._nav_toolbar._refresh_btn.click()
            self.app.processEvents()
            self.assertTrue(panel.table.hasFocus())

        host.close()
        host.deleteLater()

    def test_remote_context_menu_path_shortcuts_and_terminal_text_actions(self) -> None:
        table = RemoteFileTable()
        table.set_path('/srv/project')
        table.set_list_callback(lambda _path: [
            {'name': 'hello world.txt', 'is_dir': False, 'size': 3},
        ])
        table.refresh()
        item = next(
            table.item(row, 0)
            for row in range(table.rowCount())
            if table.item(row, 0) is not None
            and table.item(row, 0).text() == 'hello world.txt'
        )
        table.setCurrentItem(item)
        item.setSelected(True)
        pos = table.visualItemRect(item).center()
        emitted: list[str] = []
        changed_paths: list[str] = []
        terminal_path_requests: list[bool] = []
        table.terminal_text_requested.connect(emitted.append)
        table.terminal_path_change_requested.connect(changed_paths.append)
        table.terminal_path_requested.connect(lambda: terminal_path_requests.append(True))
        captured = {}

        def capture_menu(menu, _global_pos):
            captured.update(menu._key_actions)
            captured['actions'] = menu.actions()
            return None

        with patch('ui.file_panel.remote_table.exec_menu', side_effect=capture_menu):
            table._show_context_menu(pos)

        QApplication.clipboard().clear()
        captured[Qt.Key_A].trigger()
        self.assertEqual(QApplication.clipboard().text(), '/srv/project/hello world.txt')
        captured[Qt.Key_P].trigger()
        self.assertEqual(QApplication.clipboard().text(), '/srv/project')

        captured[Qt.Key_S].trigger()
        captured[Qt.Key_F].trigger()
        captured[Qt.Key_G].trigger()
        self.assertEqual(
            emitted,
            ["'hello world.txt'", "'/srv/project/hello world.txt'", '/srv/project'],
        )
        self.assertEqual(
            captured[Qt.Key_Q].text().split('\t')[0],
            'Change Terminal Path to This',
        )
        captured[Qt.Key_Q].trigger()
        self.assertEqual(changed_paths, ['/srv/project'])
        self.assertEqual(
            captured[Qt.Key_W].text().split('\t')[0],
            'Go to Terminal Path',
        )
        captured[Qt.Key_W].trigger()
        self.assertEqual(terminal_path_requests, [True])

        actions = captured['actions']
        change_index = actions.index(captured[Qt.Key_Q])
        self.assertIs(actions[change_index + 1], captured[Qt.Key_W])

    def test_remote_terminal_text_actions_reject_control_characters(self) -> None:
        table = RemoteFileTable()
        emitted: list[str] = []
        table.terminal_text_requested.connect(emitted.append)

        table._send_names_to_terminal([('safe\nwhoami', 'file')])
        table._send_paths_to_terminal([('safe\x1b[2J', 'file')])

        self.assertEqual(emitted, [])

    def test_local_context_menu_uses_path_a_and_parent_path_p(self) -> None:
        table = LocalFileTable(initial_path=str(Path.cwd()))
        table.refresh()
        item = next(
            table.item(row, 0)
            for row in range(table.rowCount())
            if table.item(row, 0) is not None and table.item(row, 0).text() != '..'
        )
        table.setCurrentItem(item)
        item.setSelected(True)
        pos = table.visualItemRect(item).center()
        captured = {}

        def capture_menu(menu, _global_pos):
            captured.update(menu._key_actions)
            return None

        with patch('ui.file_panel.local_table.exec_menu', side_effect=capture_menu):
            table._show_context_menu(pos)

        self.assertEqual(captured[Qt.Key_A].text().split('\t')[0], 'Copy Path')
        self.assertEqual(captured[Qt.Key_P].text().split('\t')[0], 'Copy Parent Path')

    def test_filter_edit_arrows_navigate_only_visible_rows(self) -> None:
        table = RemoteFileTable()
        table.set_list_callback(lambda _path: [
            {'name': 'alpha.txt', 'is_dir': False, 'size': 1},
            {'name': 'beta.txt', 'is_dir': False, 'size': 1},
            {'name': 'gamma.txt', 'is_dir': False, 'size': 1},
        ])
        table.refresh()
        statusbar = _FilePanelStatusBar(table, transfer_kind='download')
        statusbar.show()
        statusbar.file_filter_edit.setText('*a*')
        statusbar.focus_filter()
        self.app.processEvents()
        edit = statusbar.file_filter_edit
        visible_rows = [row for row in range(table.rowCount()) if not table.isRowHidden(row)]

        def selected_rows() -> list[int]:
            return sorted({index.row() for index in table.selectedIndexes()})

        table.clearSelection()
        QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[-1]])
        QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[-2]])
        for _ in range(len(visible_rows) + 1):
            QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[0]])

        table.clearSelection()
        QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[0]])
        QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[1]])
        for _ in range(len(visible_rows) + 1):
            QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[-1]])

        table.item(visible_rows[0], 0).setSelected(True)
        table.item(visible_rows[-1], 0).setSelected(True)
        self.assertGreater(len(selected_rows()), 1)
        QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[-1]])
        table.item(visible_rows[0], 0).setSelected(True)
        QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        self.assertEqual(selected_rows(), [visible_rows[0]])
        self.assertTrue(edit.hasFocus())

    def test_filter_edit_escape_keeps_single_selection_visible(self) -> None:
        table = RemoteFileTable()
        table.set_list_callback(lambda _path: [
            {'name': f'item-{index:02d}.txt', 'is_dir': False, 'size': 1}
            for index in range(30)
        ])
        table.refresh()
        statusbar = _FilePanelStatusBar(table, transfer_kind='download')
        statusbar.show()
        statusbar.file_filter_edit.setText('item-29')
        statusbar.focus_filter()
        self.app.processEvents()
        selected_item = next(
            table.item(row, 0)
            for row in range(table.rowCount())
            if not table.isRowHidden(row)
        )
        table.setCurrentItem(selected_item)
        selected_item.setSelected(True)

        with patch.object(table, 'scrollToItem') as scroll_to_item:
            QApplication.sendEvent(
                statusbar.file_filter_edit,
                QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier),
            )
            self.assertFalse(scroll_to_item.called)
            self.app.processEvents()

        self.assertEqual(table.filter_text(), '')
        self.assertTrue(statusbar.file_filter_edit.isHidden())
        self.assertTrue(selected_item.isSelected())
        scroll_to_item.assert_called_once()
        self.assertIs(scroll_to_item.call_args.args[0], selected_item)

    def test_path_change_selects_first_row_in_local_and_remote_tables(self) -> None:
        local_table = LocalFileTable(initial_path=str(Path.cwd().parent))
        local_table.set_path(str(Path.cwd()))
        local_selected = sorted({index.row() for index in local_table.selectedIndexes()})
        self.assertEqual(local_selected, [0])
        local_table.clearSelection()
        local_table.refresh()
        self.assertEqual(local_table.selectedIndexes(), [])
        local_table.set_path(str(Path.cwd() / 'README.md'))
        self.assertEqual(local_table.currentItem().text(), 'README.md')

        remote_table = RemoteFileTable()
        remote_table.set_list_callback(lambda _path: [
            {'name': 'alpha.txt', 'is_dir': False, 'size': 1},
            {'name': 'beta.txt', 'is_dir': False, 'size': 1},
        ])
        remote_table.refresh_requested.connect(remote_table.refresh)
        remote_table.set_path('/srv/project')
        remote_selected = sorted({index.row() for index in remote_table.selectedIndexes()})
        self.assertEqual(remote_selected, [0])
        remote_table.set_path('/srv/project/beta.txt')
        self.assertEqual(remote_table.currentItem().text(), 'beta.txt')

    def test_refresh_restores_existing_selection_or_selects_first_visible_row(self) -> None:
        entries = [
            {'name': 'alpha.txt', 'is_dir': False, 'size': 1},
            {'name': 'beta.txt', 'is_dir': False, 'size': 1},
            {'name': 'gamma.txt', 'is_dir': False, 'size': 1},
        ]
        remote_table = RemoteFileTable()
        remote_table.set_list_callback(lambda _path: list(entries))
        remote_table.refresh()

        def item_named(name: str):
            return next(
                remote_table.item(row, 0)
                for row in range(remote_table.rowCount())
                if remote_table.item(row, 0) is not None
                and remote_table.item(row, 0).text() == name
            )

        def selected_names() -> list[str]:
            return sorted({
                remote_table.item(index.row(), 0).text()
                for index in remote_table.selectedIndexes()
            })

        item_named('alpha.txt').setSelected(True)
        item_named('gamma.txt').setSelected(True)
        entries[:] = [
            {'name': 'beta.txt', 'is_dir': False, 'size': 1},
            {'name': 'gamma.txt', 'is_dir': False, 'size': 1},
            {'name': 'delta.txt', 'is_dir': False, 'size': 1},
        ]
        remote_table.refresh()
        self.assertEqual(selected_names(), ['gamma.txt'])

        entries[:] = [
            {'name': 'beta.txt', 'is_dir': False, 'size': 1},
            {'name': 'delta.txt', 'is_dir': False, 'size': 1},
        ]
        remote_table.refresh()
        self.assertEqual(selected_names(), ['beta.txt'])

        local_table = LocalFileTable(initial_path=str(Path.cwd()))
        local_table.refresh()
        readme = next(
            local_table.item(row, 0)
            for row in range(local_table.rowCount())
            if local_table.item(row, 0) is not None
            and local_table.item(row, 0).text() == 'README.md'
        )
        readme.setSelected(True)
        local_table.refresh()
        self.assertTrue(
            any(
                local_table.item(index.row(), 0).text() == 'README.md'
                for index in local_table.selectedIndexes()
            )
        )

    def test_missing_pending_target_is_consumed_after_one_refresh(self) -> None:
        table = RemoteFileTable()
        table.set_list_callback(lambda _path: [
            {'name': 'alpha.txt', 'is_dir': False, 'size': 1},
        ])
        table.refresh_requested.connect(table.refresh)

        table.set_path('/srv/project', select_name='missing.txt')
        self.assertEqual(table._pending_select_name, '')
        self.assertEqual(table.currentItem().text(), '..')

        table.refresh()
        self.assertEqual(table.currentItem().text(), '..')

    def test_remote_rename_failure_clears_pending_target_before_refresh(self) -> None:
        panel = RemoteFilePanel()
        panel.set_list_callback(lambda _path: [
            {'name': 'beta.txt', 'is_dir': False, 'size': 1},
        ])
        handler = SftpUiHandler('tab-a', object(), panel.refresh)
        panel.set_sftp_handler(handler)
        beta = next(
            panel.table.item(row, 0)
            for row in range(panel.table.rowCount())
            if panel.table.item(row, 0) is not None
            and panel.table.item(row, 0).text() == 'beta.txt'
        )
        panel.table.setCurrentItem(beta)
        beta.setSelected(True)
        panel.table._pending_select_name = 'taken.txt'

        handler.rename_failed.emit('taken.txt')
        panel.table.refresh()

        self.assertEqual(panel.table._pending_select_name, '')
        self.assertEqual(panel.table.currentItem().text(), 'beta.txt')

    def test_clear_remote_discards_pending_selection_state(self) -> None:
        table = RemoteFileTable()
        table._pending_select_name = 'taken.txt'
        table._select_first_after_path_change = True
        table._refresh_selection_names = ('beta.txt',)

        table.clear_remote()

        self.assertEqual(table._pending_select_name, '')
        self.assertFalse(table._select_first_after_path_change)
        self.assertIsNone(table._refresh_selection_names)

    def test_configured_remote_path_does_not_replace_remote_home(self) -> None:
        handler = SftpUiHandler('tab-a', object(), lambda: None)

        initialized = handler.try_init_session_paths(
            'C:\\work',
            '/srv/project',
            '/home/alice',
        )

        self.assertTrue(initialized)
        self.assertEqual(handler.remote_dir, '/srv/project')
        self.assertEqual(handler.remote_home, '/home/alice')

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
                14,
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
        ui_size_spin = dialog.findChild(QSpinBox, 'UiFontSizeSpin')
        config_hint = dialog.findChild(QLabel, 'SettingsConfigFileHint')
        self.assertIsNotNone(editor_edit)
        self.assertIsNotNone(size_spin)
        self.assertIsNotNone(ui_size_spin)
        self.assertIsNotNone(config_hint)
        self.assertEqual(editor_edit.text(), r'C:\Tools\editor.exe')
        self.assertEqual(size_spin.value(), 25)
        self.assertEqual(ui_size_spin.value(), 14)
        self.assertIn('config/config.json', config_hint.text())

    def test_settings_dialog_saves_ui_font_size(self) -> None:
        saved = []

        def create_dialog(parent, title, *, min_width=400):
            dialog = QDialog(parent)
            dialog.setWindowTitle(title)
            dialog.setMinimumWidth(min_width)

            def update_and_accept() -> None:
                dialog.findChild(QSpinBox, 'UiFontSizeSpin').setValue(19)
                dialog.accept()

            QTimer.singleShot(0, update_and_accept)
            return dialog

        with patch('ui.settings_dialog.create_dialog', side_effect=create_dialog):
            result = prompt_app_settings(
                None,
                'dark',
                14,
                22,
                'Consolas',
                'en',
                '',
                25,
                on_save=saved.append,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.ui_font_size_px, 19)
        self.assertEqual([settings.ui_font_size_px for settings in saved], [19])

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


class SftpUiHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_rename_failure_emits_pending_target_name(self) -> None:
        handler = SftpUiHandler(
            'tab-a',
            _FakeConnectionManager(_FakeSftp()),
            lambda: None,
        )
        failed_names: list[str] = []
        handler.rename_failed.connect(failed_names.append)

        with (
            patch(
                'ui.sftp_ui_handler.rename',
                new=AsyncMock(side_effect=OSError('denied')),
            ),
            patch.object(handler, '_warn', new=AsyncMock()),
        ):
            await handler._rename_async('beta.txt', 'taken.txt')

        self.assertEqual(failed_names, ['taken.txt'])

    async def test_remote_rename_without_session_emits_pending_target_name(self) -> None:
        connection_manager = SimpleNamespace(get_session=lambda _tab_id: None)
        handler = SftpUiHandler('tab-a', connection_manager, lambda: None)
        failed_names: list[str] = []
        handler.rename_failed.connect(failed_names.append)

        await handler._rename_async('beta.txt', 'taken.txt')

        self.assertEqual(failed_names, ['taken.txt'])


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
        async def fake_download(
            _sftp, _remote_path: str, local_path: str, *, tab_id: str
        ) -> None:
            del tab_id
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

        async def fake_download(
            _sftp, _remote_path: str, local_path: str, *, tab_id: str
        ) -> None:
            del tab_id
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

        async def fake_download(
            _sftp, _remote_path: str, target: str, *, tab_id: str
        ) -> None:
            del tab_id
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
        self.assertTrue(prompt.await_args.kwargs['foreground'])

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
