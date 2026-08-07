#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Format file permission strings for local/remote file panels."""
from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionChange:
    """Permission bit mask/value pair used by local and remote property tasks."""

    mask: int
    value: int
    recursive: bool

    def apply(self, mode: int) -> int:
        return (mode & ~self.mask) | (self.value & self.mask)


def format_unix_mode(mode: int) -> str:
    """Return ls-style permission text, e.g. drwxr-xr-x or lrwxrwxrwx."""
    return stat.filemode(mode)


def format_local_permission(path: str) -> str:
    """Format permission text for a local filesystem entry."""
    try:
        st = os.lstat(path)
    except OSError:
        return ''
    if sys.platform == 'win32':
        return _format_windows_permission(st)
    return format_unix_mode(st.st_mode)


def _format_windows_permission(st: os.stat_result) -> str:
    """Compact permission text on Windows: type + rw- / r--."""
    mode = st.st_mode
    if stat.S_ISLNK(mode):
        type_char = 'l'
    elif stat.S_ISDIR(mode):
        type_char = 'd'
    else:
        type_char = '-'
    readonly = not bool(mode & stat.S_IWRITE)
    if hasattr(st, 'st_file_attributes'):
        readonly = readonly or bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY)
    return type_char + ('r--' if readonly else 'rw-')
