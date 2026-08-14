#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File panel widgets, tables, and per-tab containers."""
from __future__ import annotations

from ui.file_panel.helpers import EqualSplitSplitter
from ui.file_panel.panels import (
    FilePanelsContainer,
    FilesPanel,
    LocalFilePanel,
    RemoteFilePanel,
)
from ui.file_panel.local_table import LocalFileTable
from ui.file_panel.remote_table import RemoteFileTable

__all__ = [
    'EqualSplitSplitter',
    'FilePanelsContainer',
    'FilesPanel',
    'LocalFilePanel',
    'LocalFileTable',
    'RemoteFilePanel',
    'RemoteFileTable',
]
