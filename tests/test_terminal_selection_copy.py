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

    def test_continuous_output_keeps_selected_counter_after_history_fills(self) -> None:
        def output(index: int) -> str:
            return (
                'index=34,asset_id=3ab11f1aa0811470c4bc69a295975080,'
                f'block_count={index}/280,local stat=missing; E2 HEAD=missing; '
                'cache_origin=unknown\r\n'
            )

        with patch.object(self.widget, '_calc_cols_rows', return_value=(80, 6)):
            self.widget._history_lines = 200
            self.widget._reset_emulator_state(80, 6)
            self.widget.write_text(''.join(output(index) for index in range(166)))
            lines = self.widget._safe_display_lines()
            row = next(y for y, line in enumerate(lines) if '165/280' in line)
            col = lines[row].index('165/280')
            abs_row = self.widget._visible_y_to_abs_y(row)
            self.widget._sel_anchor = (col, abs_row)
            self.widget._sel_head = (col + 2, abs_row)
            self.assertEqual(self.widget._selected_text(), '165')

            self.widget.write_text(output(166))

            self.assertEqual(self.widget._selected_text(), '165')
            self.assertEqual(self.widget._selection_lines(), {row - 2})

    def test_short_lines_keep_selection_when_history_first_overflows(self) -> None:
        self.widget._history_lines = 2
        self.widget._reset_emulator_state(20, 3)
        self.widget.write_text('0\r\n1\r\n2\r\n3\r\n4')
        self.widget._sel_anchor = (0, 4)
        self.widget._sel_head = (0, 4)

        for output, expected_rows in (('\r\n5', {1}), ('\r\n6\r\n7', set())):
            self.widget.write_text(output)
            self.assertEqual(self.widget._selected_text(), '4')
            self.assertEqual(self.widget._selection_lines(), expected_rows)

    def test_output_after_history_fills_preserves_scrolled_view_and_selection(self) -> None:
        self.widget._history_lines = 6
        self.widget._reset_emulator_state(20, 3)
        self.widget.write_text('\r\n'.join(str(index) for index in range(12)))
        self.widget._scroll_view(3)
        self.widget._select_all_visible()
        selected = self.widget._selected_text()
        visible = self.widget._safe_display_lines()
        viewport_top = self.widget._viewport_top_row

        self.widget.write_text('\r\n12\r\n13')

        self.assertEqual(self.widget._viewport_top_row, viewport_top)
        self.assertEqual(self.widget._safe_display_lines(), visible)
        self.assertEqual(self.widget._selected_text(), selected)
        self.widget._set_scrollbar_target(0)
        self.assertEqual(self.widget._selected_text(), selected)

    def test_evicted_selection_does_not_jump_to_new_output(self) -> None:
        self.widget._history_lines = 2
        self.widget._reset_emulator_state(20, 3)
        self.widget.write_text('0\r\n1\r\n2\r\n3\r\n4')
        self.widget._scroll_view(2)
        self.widget._sel_anchor = (0, 0)
        self.widget._sel_head = (0, 0)
        self.assertEqual(self.widget._selected_text(), '0')

        self.widget.write_text('\r\n5\r\n6\r\n7')

        self.assertEqual(self.widget._selected_text(), '')
        self.assertEqual(self.widget._selection_lines(), set())
        self.assertEqual([line.rstrip() for line in self.widget._safe_display_lines()], ['3', '4', '5'])

    def test_remote_clear_resets_coordinates_after_history_overflow(self) -> None:
        self.widget._history_lines = 2
        self.widget._reset_emulator_state(20, 3)
        self.widget.write_text('0\r\n1\r\n2\r\n3\r\n4\r\n5')
        self.widget._select_all_visible()

        self.widget.write_text('\x1b[H\x1b[2Jnew\r\noutput')

        self.assertEqual(self.widget._viewport_top_row, 0)
        self.assertEqual(self.widget._selected_text(), '')
        self.widget.write_text('\r\n2\r\n3\r\n4')
        self.assertEqual(self.widget._viewport_top_row, 2)

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
