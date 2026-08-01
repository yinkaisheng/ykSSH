#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default file panel table layout settings for config.json file_panel.*."""
from __future__ import annotations

from typing import Dict, Tuple

DEFAULT_LOCAL_COLUMN_WIDTHS: Tuple[int, ...] = (240, 96, 144)
DEFAULT_REMOTE_COLUMN_WIDTHS: Tuple[int, ...] = (200, 96, 144, 72)
DEFAULT_FILE_TABLE_HEADER_HEIGHT_PX = 24
DEFAULT_FILE_TABLE_ROW_HEIGHT_PX = 24

_FILE_PANEL_INT_BOUNDS: Dict[str, Tuple[int, int]] = {
    'header_height_px': (18, 48),
    'row_height_px': (18, 48),
}

_FILE_PANEL_INT_DEFAULTS: Dict[str, int] = {
    'header_height_px': DEFAULT_FILE_TABLE_HEADER_HEIGHT_PX,
    'row_height_px': DEFAULT_FILE_TABLE_ROW_HEIGHT_PX,
}


def default_file_panel() -> Dict[str, object]:
    return {
        'local_column_widths': list(DEFAULT_LOCAL_COLUMN_WIDTHS),
        'remote_column_widths': list(DEFAULT_REMOTE_COLUMN_WIDTHS),
        **_FILE_PANEL_INT_DEFAULTS,
    }
