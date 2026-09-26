#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import QApplication

from ui.terminal_vt_widget import TerminalVTWidget


class _FakeWheelEvent:
    def __init__(self, dy: int = 120) -> None:
        self._dy = dy
        self.accepted = False

    def modifiers(self) -> Qt.KeyboardModifiers:
        return Qt.NoModifier

    def angleDelta(self) -> QPoint:
        return QPoint(0, self._dy)

    def pixelDelta(self) -> QPoint:
        return QPoint(0, 0)

    def pos(self) -> QPoint:
        return QPoint(40, 40)

    def x(self) -> int:
        return 40

    def y(self) -> int:
        return 40

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False


class TerminalMouseReconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = TerminalVTWidget()
        self.emitted: list[bytes] = []
        self.widget.input_received.connect(self.emitted.append)

    def _enable_remote_mouse(self) -> None:
        self.widget.write_text('\x1b[?1000;1002;1006h')

    def test_disconnect_clears_stale_mouse_reporting(self) -> None:
        self._enable_remote_mouse()
        self.assertTrue(self.widget._mouse_enabled())

        self.widget.wheelEvent(_FakeWheelEvent(120))
        self.assertTrue(any(chunk.startswith(b'\x1b[<64;') for chunk in self.emitted))

        self.emitted.clear()
        self.widget.set_reconnect_enabled(True)
        self.widget.set_reconnect_enabled(False)

        self.assertFalse(self.widget._mouse_enabled())
        self.widget.wheelEvent(_FakeWheelEvent(120))
        self.assertEqual(self.emitted, [])

    def test_disconnect_exits_alt_screen_before_reconnect_prompt(self) -> None:
        self.widget.write_text('\x1b[?1049h\x1b[?1000;1006h')
        self.assertTrue(self.widget._in_alt_screen)
        self.assertTrue(self.widget._mouse_enabled())

        self.widget.reset_session_input_modes()

        self.assertFalse(self.widget._in_alt_screen)
        self.assertFalse(self.widget._mouse_enabled())
        self.assertFalse(self.widget._app_cursor_keys)
        self.assertFalse(self.widget._bracketed_paste_mode)


if __name__ == '__main__':
    unittest.main()
