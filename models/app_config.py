#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed application configuration models."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from models.favorite_path import FavoritePath


@dataclass(frozen=True)
class AppearanceConfig:
    theme: str
    ui_font_size_px: int
    table_font_size_px: int
    status_font_size_px: int
    session_tree_font_size_px: int
    session_tree_row_height_px: int
    filter_edit_height: int
    filter_edit_font_size: int
    ui_font_families_win: Tuple[str, ...]
    terminal_font_family: str
    terminal_font_size_px: int
    terminal_font_size_min: int
    terminal_font_size_max: int
    terminal_font_families: Tuple[str, ...]
    terminal_font_fallbacks: Tuple[str, ...]


@dataclass(frozen=True)
class WindowConfig:
    border_width: int
    title_bar_height: int
    tab_bar_height: int
    width: Optional[int]
    height: Optional[int]
    session_tree_width: Optional[int]
    vertical_splitter: Optional[float]


@dataclass(frozen=True)
class FilePanelConfig:
    local_column_widths: Dict[str, int]
    remote_column_widths: Dict[str, int]
    header_height_px: int
    row_height_px: int
    file_panel_toolbar_height: int
    file_panel_toolbar_font_size: int
    file_panel_statusbar_font_size: int
    file_panel_favorites_menu_font_size: int
    folder_name_bold: bool
    local_favorites: Tuple[FavoritePath, ...]
    local_favorites_dialog_width: int
    local_favorites_dialog_height: int
    remote_favorites_dialog_width: int
    remote_favorites_dialog_height: int


@dataclass(frozen=True)
class SidePanelConfig:
    session_edit_dialog_width: int
    session_edit_dialog_height: int
    command_edit_dialog_width: int
    command_edit_dialog_height: int


@dataclass(frozen=True)
class EditorConfig:
    executable_path: str
    remote_large_file_mb: int


@dataclass(frozen=True)
class AppConfig:
    language: str
    themes: Dict[str, Dict[str, str]]
    appearance: AppearanceConfig
    terminal: Dict[str, Any]
    window: WindowConfig
    file_panel: FilePanelConfig
    side_panel: SidePanelConfig
    editor: EditorConfig
