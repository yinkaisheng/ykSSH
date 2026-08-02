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
DEFAULT_LOCAL_FAVORITES_DIALOG_WIDTH = 820
DEFAULT_LOCAL_FAVORITES_DIALOG_HEIGHT = 420
DEFAULT_REMOTE_FAVORITES_DIALOG_WIDTH = 560
DEFAULT_REMOTE_FAVORITES_DIALOG_HEIGHT = 380

_FILE_PANEL_INT_BOUNDS: Dict[str, Tuple[int, int]] = {
    'header_height_px': (18, 48),
    'row_height_px': (18, 48),
    'file_panel_toolbar_height': (18, 48),
    'file_panel_toolbar_font_size': (8, 32),
    'local_favorites_dialog_width': (480, 4000),
    'local_favorites_dialog_height': (280, 3000),
    'remote_favorites_dialog_width': (360, 4000),
    'remote_favorites_dialog_height': (240, 3000),
}

_FILE_PANEL_INT_DEFAULTS: Dict[str, int] = {
    'header_height_px': DEFAULT_FILE_TABLE_HEADER_HEIGHT_PX,
    'row_height_px': DEFAULT_FILE_TABLE_ROW_HEIGHT_PX,
    'file_panel_toolbar_height': DEFAULT_FILE_PANEL_TOOLBAR_HEIGHT,
    'file_panel_toolbar_font_size': DEFAULT_FILE_PANEL_TOOLBAR_FONT_SIZE,
    'local_favorites_dialog_width': DEFAULT_LOCAL_FAVORITES_DIALOG_WIDTH,
    'local_favorites_dialog_height': DEFAULT_LOCAL_FAVORITES_DIALOG_HEIGHT,
    'remote_favorites_dialog_width': DEFAULT_REMOTE_FAVORITES_DIALOG_WIDTH,
    'remote_favorites_dialog_height': DEFAULT_REMOTE_FAVORITES_DIALOG_HEIGHT,
}

_FILE_PANEL_BOOL_DEFAULTS: Dict[str, bool] = {
    'folder_name_bold': DEFAULT_FOLDER_NAME_BOLD,
}

DEFAULT_LOCAL_FAVORITES: list = []


def default_file_panel() -> Dict[str, object]:
    return {
        'local_column_widths': list(DEFAULT_LOCAL_COLUMN_WIDTHS),
        'remote_column_widths': list(DEFAULT_REMOTE_COLUMN_WIDTHS),
        **_FILE_PANEL_INT_DEFAULTS,
        **_FILE_PANEL_BOOL_DEFAULTS,
        'local_favorites': list(DEFAULT_LOCAL_FAVORITES),
    }
