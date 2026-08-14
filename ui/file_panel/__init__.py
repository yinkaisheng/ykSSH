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
from ui.file_panel.tables import LocalFileTable, RemoteFileTable

__all__ = [
    'EqualSplitSplitter',
    'FilePanelsContainer',
    'FilesPanel',
    'LocalFilePanel',
    'LocalFileTable',
    'RemoteFilePanel',
    'RemoteFileTable',
]
