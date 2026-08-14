#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility imports for file table implementations."""
from __future__ import annotations

from ui.file_panel.base_table import _BaseFileTable
from ui.file_panel.local_table import (
    LocalFileTable,
    _is_local_root,
    _list_windows_drives,
    _windows_drive_root,
)
from ui.file_panel.remote_table import (
    RemoteFileTable,
    _is_remote_root,
    _remote_parent_and_name,
)

__all__ = [
    'LocalFileTable',
    'RemoteFileTable',
]
