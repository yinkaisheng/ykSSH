#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication, QWidget

from ui.win32_frameless import apply_taskbar_minimize_style, handle_native_event


class Win32FramelessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_handle_native_event_ignores_non_windows_messages(self) -> None:
        widget = QWidget()
        try:
            self.assertIsNone(handle_native_event(widget, b'not-windows', 0))
        finally:
            widget.deleteLater()
            self.app.processEvents()

    def test_apply_taskbar_minimize_style_is_windows_only(self) -> None:
        widget = QWidget()
        try:
            applied = apply_taskbar_minimize_style(widget)
            if sys.platform == 'win32':
                self.assertIsInstance(applied, bool)
            else:
                self.assertFalse(applied)
        finally:
            widget.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
