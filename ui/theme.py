#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, Literal, Optional

from PyQt5.QtGui import QFont, QColor, QFontMetrics
from PyQt5.QtWidgets import QApplication

from storage.app_config import get_app_config
from storage.theme_defaults import (
    DEFAULT_THEMES,
    THEME_COLOR_DOCS,
    ThemeColorDict,
    format_theme_palette_doc,
    merge_theme_colors,
)
from ui.theme_stylesheet import build_stylesheet

ThemeName = Literal['solarized', 'light', 'dark']
THEME_SOLARIZED: ThemeName = 'solarized'
THEME_LIGHT: ThemeName = 'light'
THEME_DARK: ThemeName = 'dark'
THEME_OPTIONS = [THEME_SOLARIZED, THEME_LIGHT, THEME_DARK]


@dataclass(frozen=True)
class ThemePalette:
    background_primary: str
    text_primary: str
    background_secondary: str
    background_row_stripe: str
    border: str
    border_emphasis: str
    text_heading: str
    text_secondary: str
    text_disabled: str
    background_hover: str
    background_menu_hover: str
    background_toggle_hover: str
    highlight: str
    highlight_hover: str
    highlight_pressed: str
    highlight_text: str
    table_selected_background: str
    tree_row_stripe_background: str
    tree_selected_background: str
    tab_selected_background: str
    tab_background: str
    tab_hover_background: str
    link: str
    status_error: str
    status_success: str
    status_warning: str
    status_pending: str
    table_grid: str
    title_bar_background: str
    window_border: str
    terminal_focus_border: str


ThemePalette.__doc__ = format_theme_palette_doc()


_active_palette: Optional[ThemePalette] = None


def palette_from_colors(colors: ThemeColorDict) -> ThemePalette:
    return ThemePalette(**colors)


def get_theme_palette(theme: ThemeName) -> ThemePalette:
    theme_name = normalize_theme_name(theme)
    cfg = get_app_config()
    colors = cfg.themes.get(theme_name)
    if colors is None:
        colors = merge_theme_colors(theme_name, DEFAULT_THEMES[theme_name])
    return palette_from_colors(colors)


def active_theme_palette() -> ThemePalette:
    if _active_palette is not None:
        return _active_palette
    return get_theme_palette(THEME_SOLARIZED)


def check_mark_color() -> QColor:
    return QColor(active_theme_palette().highlight_text)


def format_link_html(href: str, text: str | None = None) -> str:
    label = text if text is not None else href
    color = active_theme_palette().link
    return f'<a href="{href}" style="color: {color}; text-decoration: none;">{label}</a>'


def normalize_theme_name(theme: str | None) -> ThemeName:
    if theme == THEME_DARK:
        return THEME_DARK
    if theme == THEME_LIGHT:
        return THEME_LIGHT
    if theme == THEME_SOLARIZED:
        return THEME_SOLARIZED
    return THEME_SOLARIZED


def _appearance():
    return get_app_config().appearance


def terminal_font_size_min() -> int:
    return _appearance().terminal_font_size_min


def terminal_font_size_max() -> int:
    return _appearance().terminal_font_size_max


def default_terminal_font_family() -> str:
    return _appearance().terminal_font_family


def normalize_terminal_font_family(value) -> str:
    appearance = _appearance()
    if isinstance(value, str):
        name = value.strip().replace('"', '')
        if name:
            return name
    return appearance.terminal_font_family


def terminal_font_family_css(family: str | None = None) -> str:
    appearance = _appearance()
    name = normalize_terminal_font_family(family)
    primary = f'"{name}"' if ' ' in name else name
    fallbacks = ', '.join(
        f'"{fallback}"' if ' ' in fallback else fallback
        for fallback in appearance.terminal_font_fallbacks
        if fallback != name
    )
    return f'{primary}, {fallbacks}, monospace'


def default_ui_font() -> QFont:
    """Stable UI font; do not rely on QApplication.font() (can be SimSun 6pt on some setups)."""
    appearance = _appearance()
    font = QFont()
    if sys.platform == 'win32':
        for family in appearance.ui_font_families_win:
            candidate = QFont(family)
            if candidate.exactMatch():
                font = candidate
                break
    elif sys.platform == 'darwin':
        candidate = QFont('.AppleSystemUIFont')
        if candidate.exactMatch():
            font = candidate
    else:
        font = QFont('Sans Serif')
    font.setPixelSize(appearance.ui_font_size_px)
    return font


def apply_app_font(app: QApplication) -> None:
    """Apply the app-wide font once at startup."""
    app.setFont(default_ui_font())


def normalize_terminal_font_size(value) -> int:
    appearance = _appearance()
    minimum = appearance.terminal_font_size_min
    maximum = appearance.terminal_font_size_max
    if isinstance(value, int) and minimum <= value <= maximum:
        return value
    return appearance.terminal_font_size_px


def apply_app_theme(
    app: QApplication,
    theme: str | None = THEME_SOLARIZED,
    terminal_font_size: int | None = None,
    terminal_font_family: str | None = None,
) -> None:
    global _active_palette
    theme_name = normalize_theme_name(theme)
    palette = get_theme_palette(theme_name)
    _active_palette = palette
    app.setStyleSheet(
        _build_stylesheet(
            app.font(),
            palette,
            terminal_font_size,
            terminal_font_family,
        )
    )


def apply_main_window_border(shell_frame, border_color: str, border_width: int = 2) -> None:
    """Draw an outer border on the frameless window shell frame."""
    color = border_color.strip() if isinstance(border_color, str) else ''
    try:
        width = int(border_width)
    except (TypeError, ValueError):
        width = 2
    width = max(0, min(8, width))
    shell_frame.setObjectName('MainShellFrame')
    if width <= 0 or not color:
        shell_frame.setStyleSheet('QFrame#MainShellFrame { border: none; }')
        return
    shell_frame.setStyleSheet(
        f'QFrame#MainShellFrame {{ border: {width}px solid {color}; background: transparent; }}'
    )


def build_window_menu_bar_stylesheet(*, menu_height: int, font: QFont | None = None) -> str:
    """Menu bar item padding + hover/selected colors (widget-local QSS overrides app QSS)."""
    p = active_theme_palette()
    metrics = QFontMetrics(font or default_ui_font())
    item_pad_v = max(0, (menu_height - metrics.height()) // 2)
    return f'''
QMenuBar#WindowMenuBar::item {{
    padding-top: {item_pad_v}px;
    padding-bottom: {item_pad_v}px;
    padding-left: 10px;
    padding-right: 10px;
    background: transparent;
    border-radius: 0px;
}}
QMenuBar#WindowMenuBar::item:hover {{
    background-color: {p.highlight};
    color: {p.highlight_text};
}}
QMenuBar#WindowMenuBar::item:selected {{
    background-color: {p.highlight};
    color: {p.highlight_text};
}}
QMenuBar#WindowMenuBar::item:pressed {{
    background-color: {p.highlight_pressed};
    color: {p.highlight_text};
}}
'''


def apply_window_title_bar(
    title_bar,
    *,
    height: int,
    border_width: int = 0,
) -> None:
    """Apply title bar height, menu bar theme, and window-control insets."""
    try:
        resolved_height = int(height)
    except (TypeError, ValueError):
        resolved_height = 32
    resolved_height = max(24, min(48, resolved_height))
    try:
        resolved_border = int(border_width)
    except (TypeError, ValueError):
        resolved_border = 0
    title_bar.apply_layout(resolved_height, border_width=resolved_border)
    content_height = max(16, resolved_height - resolved_border - title_bar.BOTTOM_BORDER_PX)
    title_bar.menu_bar.setStyleSheet(
        build_window_menu_bar_stylesheet(
            menu_height=content_height,
            font=title_bar.menu_bar.font(),
        )
    )


def resolve_ui_font_size_px() -> int:
    """Pixel size for global QSS and combo popups."""
    return _appearance().ui_font_size_px


def table_font_size_px(*, header_table: bool = False) -> int:
    appearance = _appearance()
    if header_table:
        return appearance.ui_font_size_px
    return appearance.table_font_size_px


def popup_list_font(font: QFont | None = None) -> QFont:
    """Font for combo popups; QSS alone does not reliably style popup views."""
    app = QApplication.instance()
    base = font or (app.font() if app is not None else QFont())
    ui_font = QFont(base)
    ui_font.setPixelSize(resolve_ui_font_size_px())
    return ui_font


def _build_stylesheet(
    font: QFont,
    palette: ThemePalette,
    terminal_font_size: int | None = None,
    terminal_font_family: str | None = None,
) -> str:
    appearance = _appearance()
    ui_size_px = appearance.ui_font_size_px
    tab_bar_height = get_app_config().window.tab_bar_height
    tab_padding_y = max(2, (tab_bar_height - ui_size_px - 2) // 2)
    session_tree_row_height = appearance.session_tree_row_height_px
    filter_edit_font_size = appearance.filter_edit_font_size
    filter_edit_height = appearance.filter_edit_height
    _filter_edit_font = QFont()
    _filter_edit_font.setPixelSize(filter_edit_font_size)
    _filter_edit_fm = QFontMetrics(_filter_edit_font)
    filter_edit_pad_y = max(
        0, (filter_edit_height - 2 - _filter_edit_fm.lineSpacing()) // 2,
    )
    file_panel_toolbar_font_size = get_app_config().file_panel.file_panel_toolbar_font_size
    file_panel_toolbar_height = get_app_config().file_panel.file_panel_toolbar_height
    file_panel_statusbar_font_size = get_app_config().file_panel.file_panel_statusbar_font_size
    _fp_toolbar_font = QFont()
    _fp_toolbar_font.setPixelSize(file_panel_toolbar_font_size)
    _fp_toolbar_fm = QFontMetrics(_fp_toolbar_font)
    file_panel_toolbar_pad_y = max(
        0, (file_panel_toolbar_height - 2 - _fp_toolbar_fm.lineSpacing()) // 2,
    )
    if terminal_font_size is None:
        resolved_terminal_font_size = appearance.terminal_font_size_px
    else:
        resolved_terminal_font_size = normalize_terminal_font_size(terminal_font_size)
    terminal_font_family_css_value = terminal_font_family_css(terminal_font_family)
    ui_font_family = font.family().replace('"', '\\"')
    p = palette
    return build_stylesheet(
        appearance=appearance,
        p=p,
        ui_size_px=ui_size_px,
        tab_bar_height=tab_bar_height,
        tab_padding_y=tab_padding_y,
        session_tree_row_height=session_tree_row_height,
        filter_edit_font_size=filter_edit_font_size,
        filter_edit_pad_y=filter_edit_pad_y,
        file_panel_toolbar_font_size=file_panel_toolbar_font_size,
        file_panel_toolbar_pad_y=file_panel_toolbar_pad_y,
        file_panel_statusbar_font_size=file_panel_statusbar_font_size,
        resolved_terminal_font_size=resolved_terminal_font_size,
        terminal_font_family_css_value=terminal_font_family_css_value,
        ui_font_family=ui_font_family,
    )
