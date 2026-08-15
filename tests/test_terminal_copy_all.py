#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pyte  # type: ignore
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QApplication

from ui.terminal_vt_widget import TerminalVTWidget


class TerminalCopyAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = TerminalVTWidget()
        screen = pyte.HistoryScreen(10, 3, history=20)  # type: ignore[attr-defined]
        stream = pyte.Stream(screen)  # type: ignore[attr-defined]
        stream.feed('one\r\ntwo\r\nthree\r\nfour\r\nfive')
        screen.prev_page()
        self.widget._main_screen = screen
        self.widget.screen = screen
        self.widget._in_alt_screen = False
        QGuiApplication.clipboard().clear()

    def test_context_menu_copy_all_includes_scrollback_and_visible_content(self) -> None:
        def trigger_copy_all(menu, _global_pos):
            menu.actions()[1].trigger()
            return menu.actions()[1]

        event = SimpleNamespace(globalPos=lambda: QPoint(0, 0))
        with patch('ui.terminal_vt_widget.exec_menu', side_effect=trigger_copy_all):
            self.widget.contextMenuEvent(event)

        self.assertEqual(
            QGuiApplication.clipboard().text(),
            'one\ntwo\nthree\nfour\nfive',
        )

    def test_context_menu_shortcuts_use_copy_all_a_and_select_all_s(self) -> None:
        shortcuts: dict[int, object] = {}

        def capture_shortcuts(menu, _global_pos):
            shortcuts.update(menu._key_actions)
            return None

        event = SimpleNamespace(globalPos=lambda: QPoint(0, 0))
        with patch('ui.terminal_vt_widget.exec_menu', side_effect=capture_shortcuts):
            self.widget.contextMenuEvent(event)

        self.assertEqual(shortcuts[Qt.Key_A].text().split('\t')[0], 'Copy All')
        self.assertEqual(shortcuts[Qt.Key_S].text().split('\t')[0], 'Select All')

    def test_change_directory_clears_input_and_executes_quoted_cd(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)

        self.widget.change_directory("/srv/hello world's files")

        self.assertEqual(
            emitted,
            [b'\x01\x0b', b'cd -- \'/srv/hello world\'"\'"\'s files\'\r'],
        )

    def test_change_directory_preserves_spaces_and_rejects_control_characters(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)

        self.widget.change_directory('/srv/trailing ')
        self.widget.change_directory('/srv/unsafe\nwhoami')

        self.assertEqual(
            emitted,
            [b'\x01\x0b', b"cd -- '/srv/trailing '\r"],
        )

    def test_change_directory_does_not_reuse_previous_command_start(self) -> None:
        screen = pyte.HistoryScreen(20, 3, history=20)  # type: ignore[attr-defined]
        pyte.Stream(screen).feed('$ abcdef')  # type: ignore[attr-defined]
        screen.cursor.x = 4
        self.widget._main_screen = screen
        self.widget.screen = screen
        submitted: list[tuple[str, int]] = []
        self.widget.command_submitted.connect(
            lambda command, _sent_at, row: submitted.append((command, row))
        )
        self.widget._pending_command_start_y = 7
        self.widget._pending_command_start_x = 4
        self.widget._pending_command_text = 'old input'

        self.widget.change_directory('/srv/project')

        self.assertNotIn(7, self.widget._command_start_rows)
        self.assertNotIn(7, [row for _command, row in submitted])
        self.assertEqual([command for command, _row in submitted], ['cd -- /srv/project'])


if __name__ == '__main__':
    unittest.main()
