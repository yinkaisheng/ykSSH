#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PyQt5.QtGui import QColor

from storage.app_config import _normalize_terminal
from ui import terminal_vt_widget


class TerminalGutterCommandMarkerTests(unittest.TestCase):
    def test_terminal_config_has_command_marker_color(self) -> None:
        terminal = _normalize_terminal({})
        self.assertEqual(
            terminal.get('terminal_gutter_command_background_color'),
            '#606060',
        )

    def test_visible_marker_rows_follow_viewport(self) -> None:
        visible_rows = getattr(
            terminal_vt_widget,
            '_visible_command_marker_rows',
            lambda *_args, **_kwargs: None,
        )

        self.assertEqual(visible_rows([4, 7, 11], 5, 5), (2,))
        self.assertEqual(visible_rows([4, 7, 11], 2, 5), (2,))
        self.assertEqual(visible_rows([4, 7, 11], 7, 5), (0, 4))

    def test_alt_screen_has_no_command_markers(self) -> None:
        visible_rows = getattr(
            terminal_vt_widget,
            '_visible_command_marker_rows',
            lambda *_args, **_kwargs: None,
        )

        self.assertEqual(
            visible_rows([4, 7], 4, 5, in_alt_screen=True),
            (),
        )

    def test_draws_one_full_gutter_row_for_committed_command_start(self) -> None:
        fills: list[tuple[float, float, float, float, str]] = []

        class Painter:
            def fillRect(self, rect, color: QColor) -> None:
                fills.append((rect.x(), rect.y(), rect.width(), rect.height(), color.name()))

        widget = SimpleNamespace(
            _in_alt_screen=False,
            _command_start_rows=[4, 7, 11],
            _pending_command_start_y=8,
            _viewport_top_row=5,
            _cell_h=10.0,
            screen=SimpleNamespace(lines=5),
            _gutter_command_bg=lambda: QColor('#606060'),
        )
        draw_markers = getattr(
            terminal_vt_widget.TerminalVTWidget,
            '_draw_gutter_command_markers',
            lambda *_args, **_kwargs: None,
        )

        draw_markers(widget, Painter(), 20)

        self.assertEqual(fills, [(0.0, 20.0, 20.0, 10.0, '#606060')])


if __name__ == '__main__':
    unittest.main()
