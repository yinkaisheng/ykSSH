#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QLineEdit

from ui.main_window import MainWindow


class MainWindowShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_action_has_no_keyboard_shortcut(self) -> None:
        with patch.object(MainWindow, '_restore_session'):
            window = MainWindow()

        self.assertTrue(window._settings_action.shortcut().isEmpty())

        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def test_ctrl_l_focuses_terminal_without_intercepting_terminal_clear(self) -> None:
        with patch.object(MainWindow, '_restore_session'):
            window = MainWindow()
        self.app.removeEventFilter(window)
        window.show()
        window.activateWindow()
        _tab_id, terminal = window.terminal_tabs.add_terminal_tab('test')
        other = QLineEdit(window)
        other.show()
        other.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()

        focus_event = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_L,
            Qt.ControlModifier,
        )
        self.assertTrue(window.eventFilter(other, focus_event))
        self.assertTrue(terminal.hasFocus())

        emitted: list[bytes] = []
        terminal.input_received.connect(emitted.append)
        clear_event = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_L,
            Qt.ControlModifier,
        )
        self.assertFalse(window.eventFilter(terminal, clear_event))
        terminal.keyPressEvent(clear_event)
        self.assertEqual(emitted, [b'\x0c'])

        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def test_ctrl_alt_b_focuses_available_file_table_only_from_terminal(self) -> None:
        with patch.object(MainWindow, '_restore_session'):
            window = MainWindow()
        self.app.removeEventFilter(window)
        window.show()
        window.activateWindow()
        tab_id, terminal = window.terminal_tabs.add_terminal_tab('test', tab_id='tab-a')
        panel = window.file_panels.create_panel(tab_id)
        window.file_panels.show_panel(tab_id)
        window._active_tab_id = tab_id
        panel.remote_file_panel.set_list_callback(lambda _path: [])
        self.app.processEvents()

        terminal.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()
        shortcut = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_B,
            Qt.ControlModifier | Qt.AltModifier,
        )
        self.assertTrue(window.eventFilter(terminal, shortcut))
        self.assertTrue(panel.remote_file_panel.table.hasFocus())

        panel.remote_file_panel.clear_remote()
        terminal.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()
        disconnected_shortcut = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_B,
            Qt.ControlModifier | Qt.AltModifier,
        )
        self.assertTrue(window.eventFilter(terminal, disconnected_shortcut))
        self.assertTrue(panel.local_file_panel.table.hasFocus())

        other = QLineEdit(window)
        other.show()
        other.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()
        unrelated = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_B,
            Qt.ControlModifier | Qt.AltModifier,
        )
        self.assertFalse(window.eventFilter(other, unrelated))

        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def test_ctrl_semicolon_and_apostrophe_focus_fixed_file_tables_from_terminal(self) -> None:
        with patch.object(MainWindow, '_restore_session'):
            window = MainWindow()
        self.app.removeEventFilter(window)
        window.show()
        window.activateWindow()
        tab_id, terminal = window.terminal_tabs.add_terminal_tab('test', tab_id='tab-a')
        panel = window.file_panels.create_panel(tab_id)
        window.file_panels.show_panel(tab_id)
        window._active_tab_id = tab_id
        panel.remote_file_panel.set_list_callback(lambda _path: [])
        self.app.processEvents()

        terminal.setFocus(Qt.OtherFocusReason)
        local_shortcut = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Semicolon,
            Qt.ControlModifier,
        )
        self.assertTrue(window.eventFilter(terminal, local_shortcut))
        self.assertTrue(panel.local_file_panel.table.hasFocus())

        terminal.setFocus(Qt.OtherFocusReason)
        remote_shortcut = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Apostrophe,
            Qt.ControlModifier,
        )
        self.assertTrue(window.eventFilter(terminal, remote_shortcut))
        self.assertTrue(panel.remote_file_panel.table.hasFocus())

        panel.remote_file_panel.clear_remote()
        terminal.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()
        unavailable_remote_shortcut = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Apostrophe,
            Qt.ControlModifier,
        )
        self.assertTrue(window.eventFilter(terminal, unavailable_remote_shortcut))
        self.assertTrue(terminal.hasFocus())

        for key, modifiers in (
            (Qt.Key_N, Qt.ControlModifier | Qt.AltModifier),
            (Qt.Key_M, Qt.ControlModifier | Qt.AltModifier),
            (Qt.Key_Comma, Qt.ControlModifier),
            (Qt.Key_Period, Qt.ControlModifier),
        ):
            old_shortcut = QKeyEvent(
                QEvent.KeyPress,
                key,
                modifiers,
            )
            self.assertFalse(window.eventFilter(terminal, old_shortcut))

        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def test_terminal_receives_apostrophe_shortcut_without_application_filter(self) -> None:
        with patch.object(MainWindow, '_restore_session'):
            window = MainWindow()
        self.app.removeEventFilter(window)
        window.show()
        window.activateWindow()
        tab_id, terminal = window.terminal_tabs.add_terminal_tab('test', tab_id='tab-a')
        panel = window.file_panels.create_panel(tab_id)
        window.file_panels.show_panel(tab_id)
        window._active_tab_id = tab_id
        panel.remote_file_panel.set_list_callback(lambda _path: [])
        terminal.setFocus(Qt.OtherFocusReason)
        self.app.processEvents()

        shortcut = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Apostrophe,
            Qt.ControlModifier,
            "'",
        )
        QApplication.sendEvent(terminal, shortcut)

        self.assertTrue(shortcut.isAccepted())
        self.assertTrue(panel.remote_file_panel.table.hasFocus())

        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def test_ctrl_colon_and_quote_do_not_trigger_file_focus(self) -> None:
        with patch.object(MainWindow, '_restore_session'):
            window = MainWindow()
        self.app.removeEventFilter(window)
        window.show()
        window.activateWindow()
        tab_id, terminal = window.terminal_tabs.add_terminal_tab('test', tab_id='tab-a')
        panel = window.file_panels.create_panel(tab_id)
        window.file_panels.show_panel(tab_id)
        window._active_tab_id = tab_id
        panel.remote_file_panel.set_list_callback(lambda _path: [])
        self.app.processEvents()

        terminal.setFocus(Qt.OtherFocusReason)
        for key, text in (
            (Qt.Key_Colon, ':'),
            (Qt.Key_QuoteDbl, '"'),
            (Qt.Key_Semicolon, ';'),
            (Qt.Key_Apostrophe, "'"),
        ):
            self.assertFalse(window.eventFilter(
                terminal,
                QKeyEvent(
                    QEvent.KeyPress,
                    key,
                    Qt.ControlModifier | Qt.ShiftModifier,
                    text,
                ),
            ))
            self.assertTrue(terminal.hasFocus())

        window.hide()
        window.deleteLater()
        self.app.processEvents()

    def test_remote_file_text_signal_fills_matching_terminal_without_execute(self) -> None:
        with patch.object(MainWindow, '_restore_session'):
            window = MainWindow()
        tab_id, terminal = window.terminal_tabs.add_terminal_tab('test', tab_id='tab-a')
        panel = window.file_panels.create_panel(tab_id)
        window._register_files_panel(tab_id, panel)

        with patch.object(terminal, 'send_command_text') as send_command:
            panel.remote_file_panel.table.terminal_text_requested.emit('/srv/file.txt')

        send_command.assert_called_once_with('/srv/file.txt', execute=False)
        with patch.object(terminal, 'change_directory') as change_directory:
            panel.remote_file_panel.table.terminal_path_change_requested.emit('/srv/project')
        change_directory.assert_called_once_with('/srv/project')
        window.hide()
        window.deleteLater()
        self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
