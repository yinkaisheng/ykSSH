#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default side panel dialog settings for config.json side_panel.*."""
from __future__ import annotations

from typing import Dict, Tuple

DEFAULT_SESSION_EDIT_DIALOG_WIDTH = 700
DEFAULT_SESSION_EDIT_DIALOG_HEIGHT = 520
DEFAULT_COMMAND_EDIT_DIALOG_WIDTH = 480
DEFAULT_COMMAND_EDIT_DIALOG_HEIGHT = 320

_SIDE_PANEL_INT_BOUNDS: Dict[str, Tuple[int, int]] = {
    'session_edit_dialog_width': (400, 4000),
    'session_edit_dialog_height': (240, 3000),
    'command_edit_dialog_width': (360, 4000),
    'command_edit_dialog_height': (200, 3000),
}

_SIDE_PANEL_INT_DEFAULTS: Dict[str, int] = {
    'session_edit_dialog_width': DEFAULT_SESSION_EDIT_DIALOG_WIDTH,
    'session_edit_dialog_height': DEFAULT_SESSION_EDIT_DIALOG_HEIGHT,
    'command_edit_dialog_width': DEFAULT_COMMAND_EDIT_DIALOG_WIDTH,
    'command_edit_dialog_height': DEFAULT_COMMAND_EDIT_DIALOG_HEIGHT,
}


def default_side_panel() -> Dict[str, int]:
    return dict(_SIDE_PANEL_INT_DEFAULTS)
