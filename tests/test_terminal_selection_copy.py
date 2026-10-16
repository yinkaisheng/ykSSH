#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
from PyQt5.QtWidgets import QApplication

from ui.terminal_vt_widget import TerminalVTWidget


class TerminalSelectionCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = TerminalVTWidget()
        geometry = patch.object(self.widget, '_calc_cols_rows', return_value=(20, 3))
        geometry.start()
        self.addCleanup(geometry.stop)
        self.addCleanup(self.widget.close)
        self.widget._reset_emulator_state(20, 3)
        self.widget.write_text('one\r\ntwo\r\nthree\r\nfour\r\nfive\r\nsix')
        QGuiApplication.clipboard().clear()

    def test_keyboard_copy_keeps_selection_after_scrolling_it_out_of_view(self) -> None:
        self.widget._select_all_visible()
        self.widget._scroll_view(3)

        self.widget.keyPressEvent(QKeyEvent(
            QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier,
        ))

        self.assertEqual(QGuiApplication.clipboard().text(), 'four\nfive\nsix')

    def test_partial_visibility_does_not_truncate_selection(self) -> None:
        for start_scroll, delta, expected in (
            (0, 1, 'four\nfive\nsix'),
            (3, -1, 'one\ntwo\nthree'),
        ):
            with self.subTest(start_scroll=start_scroll):
                self.widget._set_scrollbar_target(start_scroll)
                self.widget._select_all_visible()
                self.widget._scroll_view(delta)
                self.widget.keyPressEvent(QKeyEvent(
                    QEvent.KeyPress, Qt.Key_Delete, Qt.ShiftModifier,
                ))
                self.assertEqual(QGuiApplication.clipboard().text(), expected)

    def test_context_copy_keeps_multiscreen_drag_selection_in_both_directions(self) -> None:
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.widget._set_scrollbar_target(0 if reverse else 3)
                start_row, end_row = (2, 0) if reverse else (0, 2)

                def mouse_event(kind: QEvent.Type, row: int) -> QMouseEvent:
                    x = 1.0
                    if reverse and kind == QEvent.MouseButtonPress:
                        x = self.widget._text_origin_x() + 2.5 * self.widget._cell_w
                    return QMouseEvent(
                        kind, QPointF(x, (row + 0.5) * self.widget._cell_h),
                        Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                        Qt.LeftButton, Qt.NoModifier,
                    )

                self.widget.mousePressEvent(mouse_event(QEvent.MouseButtonPress, start_row))
                self.widget._scroll_view(3 if reverse else -3)
                self.widget.mouseMoveEvent(mouse_event(QEvent.MouseMove, end_row))
                self.widget.mouseReleaseEvent(mouse_event(QEvent.MouseButtonRelease, end_row))
                self.widget._set_scrollbar_target(2)

                def trigger_copy(menu, _global_pos):
                    action = menu.actions()[0]
                    self.assertTrue(action.isEnabled())
                    action.trigger()

                with patch('ui.terminal_vt_widget.exec_menu', side_effect=trigger_copy):
                    self.widget.contextMenuEvent(SimpleNamespace(globalPos=lambda: QPoint(0, 0)))

                self.assertEqual(
                    QGuiApplication.clipboard().text(), 'one\ntwo\nthree\nfour\nfive\nsix',
                )

    def test_offscreen_wide_character_endpoints_are_copied_whole(self) -> None:
        self.widget._reset_emulator_state(20, 3)
        self.widget.write_text('中文\r\ntwo\r\nthree\r\nfour\r\nfive\r\n终点')
        self.widget._sel_anchor = (1, 0)
        self.widget._sel_head = (0, 5)
        self.widget._set_scrollbar_target(2)

        self.widget.copy()

        self.assertEqual(QGuiApplication.clipboard().text(), '中文\ntwo\nthree\nfour\nfive\n终')

    def test_new_output_keeps_selected_rows_in_scrollback(self) -> None:
        self.widget._select_all_visible()
        self.widget.write_text('\r\nseven\r\neight\r\nnine')

        self.widget.copy()

        self.assertEqual(QGuiApplication.clipboard().text(), 'four\nfive\nsix')

    def test_multiscreen_copy_respects_endpoint_columns(self) -> None:
        self.widget._sel_anchor = (1, 1)
        self.widget._sel_head = (2, 4)
        self.widget._set_scrollbar_target(2)

        self.widget.copy()

        self.assertEqual(QGuiApplication.clipboard().text(), 'wo\nthree\nfour\nfiv')

    def test_alt_screen_copy_does_not_include_main_screen_history(self) -> None:
        self.widget.write_text('\x1b[?1049halt one\r\nalt two\r\nalt three')
        self.widget._select_all_visible()
        self.widget._scroll_view(3)

        self.widget.copy()

        self.assertEqual(QGuiApplication.clipboard().text(), 'alt one\nalt two\nalt three')


if __name__ == '__main__':
    unittest.main()
