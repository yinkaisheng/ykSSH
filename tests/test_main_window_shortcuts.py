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


if __name__ == '__main__':
    unittest.main()
