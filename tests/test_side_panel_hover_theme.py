#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from storage.theme_defaults import DEFAULT_THEMES, is_valid_theme_color
from ui.theme import palette_from_colors
from ui.theme_stylesheet import build_stylesheet


def _stylesheet(theme_name: str = 'light') -> str:
    return build_stylesheet(
        appearance=SimpleNamespace(status_font_size_px=12),
        p=palette_from_colors(DEFAULT_THEMES[theme_name]),
        ui_size_px=13,
        tab_bar_height=30,
        tab_padding_y=4,
        session_tree_row_height=26,
        filter_edit_font_size_px=12,
        filter_edit_pad_y=3,
        file_panel_toolbar_font_size=12,
        file_panel_toolbar_pad_y=3,
        file_panel_statusbar_font_size=11,
        resolved_terminal_font_size=14,
        terminal_font_family_css_value='monospace',
        ui_font_family='sans-serif',
    )


class SidePanelHoverThemeTests(unittest.TestCase):
    def test_builtin_themes_define_valid_side_panel_hover_color(self) -> None:
        for theme_name, colors in DEFAULT_THEMES.items():
            with self.subTest(theme=theme_name):
                self.assertTrue(
                    is_valid_theme_color(colors['side_panel_item_hover_background'])
                )

    def test_stylesheet_scopes_hover_to_all_side_panel_item_views(self) -> None:
        stylesheet = _stylesheet()
        hover_color = DEFAULT_THEMES['light']['side_panel_item_hover_background']
        expected = f'''QTreeWidget#SessionTree::item:hover,
QTreeWidget#CommandTree::item:hover,
QListWidget#CommandHistoryList::item:hover {{
    background-color: {hover_color};
}}'''
        self.assertIn(expected, stylesheet)

    def test_tree_items_do_not_gain_hover_border_spacing(self) -> None:
        stylesheet = _stylesheet()
        expected = '''QTreeWidget::item {
    padding: 2px 4px;
    border: none;
    outline: none;
}'''
        self.assertIn(expected, stylesheet)

    def test_tree_selection_keeps_full_item_background_without_hover(self) -> None:
        stylesheet = _stylesheet()
        selected_color = DEFAULT_THEMES['light']['tree_selected_background']
        expected = f'''QTreeWidget::item:selected,
QTreeWidget::item:selected:!active {{
    background-color: {selected_color};
    color: {DEFAULT_THEMES['light']['text_primary']};
}}'''
        self.assertIn(expected, stylesheet)

    def test_selected_side_panel_items_keep_selected_background_on_hover(self) -> None:
        stylesheet = _stylesheet()
        selected_color = DEFAULT_THEMES['light']['tree_selected_background']
        expected = f'''QTreeWidget#SessionTree::item:selected:hover,
QTreeWidget#CommandTree::item:selected:hover,
QListWidget#CommandHistoryList::item:selected:hover {{
    background-color: {selected_color};'''
        self.assertIn(expected, stylesheet)


if __name__ == '__main__':
    unittest.main()
