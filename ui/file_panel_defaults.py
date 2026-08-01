#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default file panel table layout settings for config.json file_panel.*."""
from __future__ import annotations

from typing import Dict, Tuple

DEFAULT_LOCAL_COLUMN_WIDTHS: Tuple[int, ...] = (500, 96, 144)
DEFAULT_REMOTE_COLUMN_WIDTHS: Tuple[int, ...] = (460, 96, 144, 72)
DEFAULT_FILE_TABLE_HEADER_HEIGHT_PX = 24
DEFAULT_FILE_TABLE_ROW_HEIGHT_PX = 28
DEFAULT_FILE_PANEL_TOOLBAR_HEIGHT = 30
DEFAULT_FILE_PANEL_TOOLBAR_FONT_SIZE = 14
DEFAULT_FOLDER_NAME_BOLD = True

_FILE_PANEL_INT_BOUNDS: Dict[str, Tuple[int, int]] = {
    'header_height_px': (18, 48),
    'row_height_px': (18, 48),
    'file_panel_toolbar_height': (18, 48),
    'file_panel_toolbar_font_size': (8, 32),
}

_FILE_PANEL_INT_DEFAULTS: Dict[str, int] = {
    'header_height_px': DEFAULT_FILE_TABLE_HEADER_HEIGHT_PX,
    'row_height_px': DEFAULT_FILE_TABLE_ROW_HEIGHT_PX,
    'file_panel_toolbar_height': DEFAULT_FILE_PANEL_TOOLBAR_HEIGHT,
    'file_panel_toolbar_font_size': DEFAULT_FILE_PANEL_TOOLBAR_FONT_SIZE,
}

_FILE_PANEL_BOOL_DEFAULTS: Dict[str, bool] = {
    'folder_name_bold': DEFAULT_FOLDER_NAME_BOLD,
}


def default_file_panel() -> Dict[str, object]:
    return {
        'local_column_widths': list(DEFAULT_LOCAL_COLUMN_WIDTHS),
        'remote_column_widths': list(DEFAULT_REMOTE_COLUMN_WIDTHS),
        **_FILE_PANEL_INT_DEFAULTS,
        **_FILE_PANEL_BOOL_DEFAULTS,
    }
