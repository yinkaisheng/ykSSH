#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default appearance and font settings for config.json appearance.*."""
from __future__ import annotations



AppearanceDict = dict[str, object]

DEFAULT_THEME = 'solarized'

DEFAULT_UI_FONT_SIZE_PX = 14
UI_FONT_SIZE_MIN = 8
UI_FONT_SIZE_MAX = 32
DEFAULT_TABLE_FONT_SIZE_PX = 14
DEFAULT_STATUS_FONT_SIZE_PX = 12
DEFAULT_TREE_FONT_SIZE_PX = 14
DEFAULT_TREE_ROW_HEIGHT_PX = 26
DEFAULT_FILTER_EDIT_HEIGHT = 26
DEFAULT_FILTER_EDIT_FONT_SIZE_PX = 14
DEFAULT_UI_FONT_FAMILIES_WIN: tuple[str, ...] = (
    'Microsoft YaHei UI',
    'Segoe UI',
    'MS Shell Dlg 2',
)

DEFAULT_TERMINAL_FONT_FAMILY = 'Consolas'
DEFAULT_TERMINAL_FONT_SIZE_PX = 22
DEFAULT_TERMINAL_FONT_SIZE_MIN = 14
DEFAULT_TERMINAL_FONT_SIZE_MAX = 48
DEFAULT_TERMINAL_FONT_FAMILIES: tuple[str, ...] = (
    'Consolas',
    'Cascadia Mono',
    'Courier New',
)
DEFAULT_TERMINAL_FONT_FALLBACKS: tuple[str, ...] = (
    'Cascadia Mono',
    'Menlo',
    'Monaco',
    'Courier New',
    'monospace',
)

_APPEARANCE_INT_DEFAULTS: dict[str, int] = {
    'ui_font_size_px': DEFAULT_UI_FONT_SIZE_PX,
    'table_font_size_px': DEFAULT_TABLE_FONT_SIZE_PX,
    'status_font_size_px': DEFAULT_STATUS_FONT_SIZE_PX,
    'tree_font_size_px': DEFAULT_TREE_FONT_SIZE_PX,
    'tree_row_height_px': DEFAULT_TREE_ROW_HEIGHT_PX,
    'filter_edit_height': DEFAULT_FILTER_EDIT_HEIGHT,
    'filter_edit_font_size_px': DEFAULT_FILTER_EDIT_FONT_SIZE_PX,
    'terminal_font_size_px': DEFAULT_TERMINAL_FONT_SIZE_PX,
    'terminal_font_size_min': DEFAULT_TERMINAL_FONT_SIZE_MIN,
    'terminal_font_size_max': DEFAULT_TERMINAL_FONT_SIZE_MAX,
}

_APPEARANCE_INT_BOUNDS: dict[str, tuple[int, int]] = {
    'ui_font_size_px': (UI_FONT_SIZE_MIN, UI_FONT_SIZE_MAX),
    'table_font_size_px': (8, 32),
    'status_font_size_px': (8, 24),
    'tree_font_size_px': (8, 32),
    'tree_row_height_px': (18, 48),
    'filter_edit_height': (18, 48),
    'filter_edit_font_size_px': (8, 32),
    'terminal_font_size_px': (8, 48),
    'terminal_font_size_min': (6, 32),
    'terminal_font_size_max': (8, 72),
}


def default_appearance() -> AppearanceDict:
    return {
        'theme': DEFAULT_THEME,
        **_APPEARANCE_INT_DEFAULTS,
        'ui_font_families_win': list(DEFAULT_UI_FONT_FAMILIES_WIN),
        'terminal_font_family': DEFAULT_TERMINAL_FONT_FAMILY,
        'terminal_font_families': list(DEFAULT_TERMINAL_FONT_FAMILIES),
        'terminal_font_fallbacks': list(DEFAULT_TERMINAL_FONT_FALLBACKS),
    }
