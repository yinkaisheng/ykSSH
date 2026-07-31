#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default appearance and font settings for config.json appearance.*."""
from __future__ import annotations

from typing import Dict, Tuple

AppearanceDict = Dict[str, object]

DEFAULT_THEME = 'solarized'

DEFAULT_UI_FONT_SIZE_PX = 14
DEFAULT_TABLE_FONT_SIZE_PX = 14
DEFAULT_STATUS_FONT_SIZE_PX = 12
DEFAULT_TAB_CLOSE_FONT_SIZE_PX = 14
DEFAULT_UI_FONT_FAMILIES_WIN: Tuple[str, ...] = (
    'Microsoft YaHei UI',
    'Segoe UI',
    'MS Shell Dlg 2',
)

DEFAULT_BODY_TEXT_FONT_FAMILY = 'Consolas'
DEFAULT_BODY_TEXT_FONT_SIZE_PX = 18
DEFAULT_BODY_TEXT_FONT_SIZE_MIN = 14
DEFAULT_BODY_TEXT_FONT_SIZE_MAX = 48
DEFAULT_BODY_TEXT_FONT_FAMILIES: Tuple[str, ...] = (
    'Consolas',
    'Cascadia Mono',
    'Courier New',
)
DEFAULT_BODY_TEXT_FONT_FALLBACKS: Tuple[str, ...] = (
    'Cascadia Mono',
    'Menlo',
    'Monaco',
    'Courier New',
    'monospace',
)

_APPEARANCE_INT_DEFAULTS: Dict[str, int] = {
    'ui_font_size_px': DEFAULT_UI_FONT_SIZE_PX,
    'table_font_size_px': DEFAULT_TABLE_FONT_SIZE_PX,
    'status_font_size_px': DEFAULT_STATUS_FONT_SIZE_PX,
    'tab_close_font_size_px': DEFAULT_TAB_CLOSE_FONT_SIZE_PX,
    'body_text_font_size_px': DEFAULT_BODY_TEXT_FONT_SIZE_PX,
    'body_text_font_size_min': DEFAULT_BODY_TEXT_FONT_SIZE_MIN,
    'body_text_font_size_max': DEFAULT_BODY_TEXT_FONT_SIZE_MAX,
}

_APPEARANCE_INT_BOUNDS: Dict[str, Tuple[int, int]] = {
    'ui_font_size_px': (8, 32),
    'table_font_size_px': (8, 32),
    'status_font_size_px': (8, 24),
    'tab_close_font_size_px': (8, 24),
    'body_text_font_size_px': (8, 48),
    'body_text_font_size_min': (6, 32),
    'body_text_font_size_max': (8, 72),
}


def default_appearance() -> AppearanceDict:
    return {
        'theme': DEFAULT_THEME,
        **_APPEARANCE_INT_DEFAULTS,
        'ui_font_families_win': list(DEFAULT_UI_FONT_FAMILIES_WIN),
        'body_text_font_family': DEFAULT_BODY_TEXT_FONT_FAMILY,
        'body_text_font_families': list(DEFAULT_BODY_TEXT_FONT_FAMILIES),
        'body_text_font_fallbacks': list(DEFAULT_BODY_TEXT_FONT_FALLBACKS),
    }
