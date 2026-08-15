#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default file panel table layout settings for config.json file_panel.*."""
from __future__ import annotations

from typing import Any, Iterable

FILE_TABLE_COLUMN_NAME = 'Name'
FILE_TABLE_COLUMN_SIZE = 'Size'
FILE_TABLE_COLUMN_MODIFIED = 'Modified'
FILE_TABLE_COLUMN_PERMISSIONS = 'Permissions'

FILE_TABLE_COLUMNS: tuple[str, ...] = (
    FILE_TABLE_COLUMN_NAME,
    FILE_TABLE_COLUMN_SIZE,
    FILE_TABLE_COLUMN_MODIFIED,
    FILE_TABLE_COLUMN_PERMISSIONS,
)

DEFAULT_LOCAL_COLUMN_WIDTHS: dict[str, int] = {
    FILE_TABLE_COLUMN_NAME: 460,
    FILE_TABLE_COLUMN_SIZE: 96,
    FILE_TABLE_COLUMN_MODIFIED: 144,
    FILE_TABLE_COLUMN_PERMISSIONS: 100,
}

DEFAULT_REMOTE_COLUMN_WIDTHS: dict[str, int] = dict(DEFAULT_LOCAL_COLUMN_WIDTHS)

DEFAULT_FILE_TABLE_HEADER_HEIGHT_PX = 22
DEFAULT_FILE_TABLE_ROW_HEIGHT_PX = 24
DEFAULT_FILE_PANEL_TOOLBAR_HEIGHT = 26
DEFAULT_FILE_PANEL_TOOLBAR_FONT_SIZE = 14
DEFAULT_FILE_PANEL_STATUSBAR_FONT_SIZE = 13
DEFAULT_FILE_PANEL_FAVORITES_MENU_FONT_SIZE = 14
DEFAULT_FILE_PANEL_FOLDER_NAME_BOLD = True
DEFAULT_LOCAL_FAVORITES_DIALOG_WIDTH = 820
DEFAULT_LOCAL_FAVORITES_DIALOG_HEIGHT = 420
DEFAULT_REMOTE_FAVORITES_DIALOG_WIDTH = 560
DEFAULT_REMOTE_FAVORITES_DIALOG_HEIGHT = 380

_FILE_PANEL_INT_BOUNDS: dict[str, tuple[int, int]] = {
    'file_table_header_height': (18, 48),
    'file_table_row_height': (18, 48),
    'file_panel_toolbar_height': (18, 48),
    'file_panel_toolbar_font_size': (8, 32),
    'file_panel_statusbar_font_size': (8, 32),
    'file_panel_favorites_menu_font_size': (8, 32),
    'local_favorites_dialog_width': (480, 4000),
    'local_favorites_dialog_height': (280, 3000),
    'remote_favorites_dialog_width': (360, 4000),
    'remote_favorites_dialog_height': (240, 3000),
}

_FILE_PANEL_INT_DEFAULTS: dict[str, int] = {
    'file_table_header_height': DEFAULT_FILE_TABLE_HEADER_HEIGHT_PX,
    'file_table_row_height': DEFAULT_FILE_TABLE_ROW_HEIGHT_PX,
    'file_panel_toolbar_height': DEFAULT_FILE_PANEL_TOOLBAR_HEIGHT,
    'file_panel_toolbar_font_size': DEFAULT_FILE_PANEL_TOOLBAR_FONT_SIZE,
    'file_panel_statusbar_font_size': DEFAULT_FILE_PANEL_STATUSBAR_FONT_SIZE,
    'file_panel_favorites_menu_font_size': DEFAULT_FILE_PANEL_FAVORITES_MENU_FONT_SIZE,
    'local_favorites_dialog_width': DEFAULT_LOCAL_FAVORITES_DIALOG_WIDTH,
    'local_favorites_dialog_height': DEFAULT_LOCAL_FAVORITES_DIALOG_HEIGHT,
    'remote_favorites_dialog_width': DEFAULT_REMOTE_FAVORITES_DIALOG_WIDTH,
    'remote_favorites_dialog_height': DEFAULT_REMOTE_FAVORITES_DIALOG_HEIGHT,
}

_FILE_PANEL_BOOL_DEFAULTS: dict[str, bool] = {
    'file_panel_folder_name_bold': DEFAULT_FILE_PANEL_FOLDER_NAME_BOLD,
}

DEFAULT_LOCAL_FAVORITES: list = []


def clamp_column_width(value: Any, default: int) -> int:
    try:
        width = int(value)
    except (TypeError, ValueError):
        return default
    return max(40, min(2000, width))


def ordered_column_widths(
    widths: dict[str, int],
    *,
    columns: tuple[str, ...] = FILE_TABLE_COLUMNS,
    defaults: dict[str, int],
) -> tuple[int, ...]:
    """Resolve column-width dict to widths in table display order."""
    return tuple(
        widths.get(column, defaults.get(column, 100))
        for column in columns
    )


def column_widths_from_table(
    table_widths: Iterable[int],
    *,
    columns: tuple[str, ...] = FILE_TABLE_COLUMNS,
) -> dict[str, int]:
    """Build a column-width dict from current table column indices."""
    widths_list = list(table_widths)
    resolved: dict[str, int] = {}
    for index, column in enumerate(columns):
        if index >= len(widths_list):
            break
        try:
            resolved[column] = int(widths_list[index])
        except (TypeError, ValueError):
            continue
    return resolved


def default_file_panel() -> dict[str, object]:
    return {
        'local_column_widths': dict(DEFAULT_LOCAL_COLUMN_WIDTHS),
        'remote_column_widths': dict(DEFAULT_REMOTE_COLUMN_WIDTHS),
        **_FILE_PANEL_INT_DEFAULTS,
        **_FILE_PANEL_BOOL_DEFAULTS,
        'local_favorites': list(DEFAULT_LOCAL_FAVORITES),
    }
