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
    return f'''
* {{
    font-size: {ui_size_px}px;
}}

QMainWindow, QWidget {{
    background-color: {p.background_primary};
    color: {p.text_primary};
}}

QWidget#WindowTitleBar {{
    background-color: {p.title_bar_background};
    border-bottom: 1px solid {p.border};
}}

QMenuBar {{
    background-color: {p.background_secondary};
    border-bottom: 1px solid {p.border};
    padding: 2px;
}}

QMenuBar#WindowMenuBar {{
    background-color: transparent;
    border: none;
    padding: 0px;
}}

QMenuBar#WindowMenuBar::item {{
    background: transparent;
    padding: 0px 10px;
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

QWidget#WindowTitleControls {{
    background: transparent;
}}

QLabel#WindowTitleLabel {{
    color: {p.text_secondary};
    background: transparent;
}}

QToolButton#WindowTitleButton {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
    margin: 0px;
    background-image: none;
}}

QToolButton#WindowTitleButton:hover {{
    background-color: {p.background_hover};
    border-radius: 0px;
    background-image: none;
}}

QToolButton#WindowTitleButton:pressed {{
    background-color: {p.border};
    border-radius: 0px;
    background-image: none;
}}

QToolButton#WindowCloseButton {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
    margin: 0px;
    background-image: none;
}}

QToolButton#WindowCloseButton:hover {{
    background-color: #e81123;
    color: #ffffff;
    border: none;
    border-radius: 0px;
    background-image: none;
}}

QToolButton#WindowCloseButton:pressed {{
    background-color: #c50f1f;
    color: #ffffff;
    border: none;
    border-radius: 0px;
    background-image: none;
}}

QMenuBar::item:selected {{
    background-color: {p.highlight};
    color: {p.highlight_text};
    border-radius: 0px;
}}

QMenu {{
    background-color: {p.background_primary};
    border: 1px solid {p.border};
    padding: 4px;
}}

QMenu::item {{
    color: {p.text_primary};
    padding: 5px 28px 5px 12px;
    border-radius: 0px;
}}

QMenu::item:disabled {{
    color: {p.text_disabled};
}}

QMenu::item:hover,
QMenu::item:selected {{
    background-color: {p.highlight};
    color: {p.highlight_text};
}}

QMenu::item:selected:disabled {{
    background-color: transparent;
    color: {p.text_disabled};
}}

QStatusBar {{
    background-color: {p.background_secondary};
    color: {p.text_heading};
    border-top: 1px solid {p.border};
}}

QSplitter::handle {{
    background-color: {p.border};
    width: 2px;
}}

QSplitter::handle:hover {{
    background-color: {p.highlight};
}}

QSplitter#contentSplitter {{
    background-color: {p.background_primary};
}}

QSplitter#contentSplitter::handle:horizontal {{
    background-color: {p.background_primary};
    width: 4px;
    margin: 0;
    border: none;
}}

QSplitter#contentSplitter::handle:horizontal:hover {{
    background-color: {p.background_primary};
}}

QSplitter#filePanelSplitter {{
    background-color: {p.background_primary};
}}

QSplitter#filePanelSplitter::handle:horizontal {{
    background-color: {p.background_primary};
    width: 4px;
    margin: 0;
    border: none;
}}

QSplitter#filePanelSplitter::handle:horizontal:hover {{
    background-color: {p.background_primary};
}}

QSplitter#favoritesDialogSplitter {{
    background-color: {p.background_primary};
}}

QSplitter#favoritesDialogSplitter::handle:horizontal {{
    background-color: {p.background_primary};
    width: 4px;
    margin: 0;
    border: none;
}}

QSplitter#favoritesDialogSplitter::handle:horizontal:hover {{
    background-color: {p.background_primary};
}}

QLabel#filePanelPlaceholder {{
    border: 1px solid {p.border};
    background-color: {p.background_primary};
    color: {p.text_secondary};
}}

QHeaderView#fileTableHeader,
QHeaderView#fileTableHeader::section {{
    font-weight: normal;
}}

QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: 0px;
    background-color: {p.background_primary};
    top: -1px;
}}

QTabWidget#TerminalTabWidget {{
    background-color: {p.title_bar_background};
}}

QTabBar {{
    background-color: {p.title_bar_background};
    border: none;
    qproperty-drawBase: 0;
}}

QTabBar#TerminalTabBar {{
    background-color: {p.title_bar_background};
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background-color: {p.tab_background};
    color: {p.text_secondary};
    border: 1px solid {p.border};
    border-top: none;
    border-bottom: none;
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
    padding: {tab_padding_y}px 14px;
    margin-right: 2px;
    min-width: 80px;
    min-height: {tab_bar_height - 4}px;
    max-height: {tab_bar_height}px;
}}

QTabBar::tab:selected {{
    background-color: {p.tab_selected_background};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-top: none;
    border-bottom: 1px solid {p.tab_selected_background};
}}

QTabBar::tab:hover:!selected {{
    background-color: {p.tab_hover_background};
    color: {p.text_heading};
}}

QTabBar::close-button {{
    width: 0;
    height: 0;
    margin: 0;
    border: none;
    image: none;
}}

QLabel#sectionTitle {{
    font-weight: bold;
    color: {p.text_heading};
    padding: 0;
    margin: 0;
}}

QPushButton#headerModeButton {{
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {p.text_secondary};
    font-weight: bold;
    padding: 2px 8px;
    margin: 0;
    min-height: 0;
}}

QPushButton#headerModeButton:checked {{
    color: {p.text_heading};
    border-bottom: 2px solid {p.highlight};
}}

QPushButton#headerModeButton:hover {{
    color: {p.text_primary};
}}

QPushButton#compactButton {{
    background-color: {p.background_secondary};
    border: 1px solid {p.border};
    border-radius: 0px;
    padding: 0px 10px;
    min-height: 0px;
    margin: 0;
}}

QPushButton#compactButton:hover {{
    background-color: {p.background_hover};
    border-color: {p.border_emphasis};
}}

QPushButton#compactButton:pressed {{
    background-color: {p.border};
}}

QPushButton#formCellButton {{
    padding: 1px 0px;
    min-height: 0px;
    margin: 0;
}}

QTableWidget QLineEdit#formCellLineEdit {{
    padding: 1px 4px;
    margin: 0px;
    min-height: 0px;
    border: none;
}}

QPushButton {{
    background-color: {p.background_secondary};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 0px;
    padding: 5px 12px;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {p.background_hover};
    border-color: {p.border_emphasis};
}}

QPushButton:pressed {{
    background-color: {p.border};
}}

QPushButton:disabled {{
    color: {p.border};
    background-color: {p.background_secondary};
}}

QPushButton#primaryButton {{
    background-color: {p.highlight};
    color: {p.highlight_text};
    border: 1px solid {p.highlight};
}}

QPushButton#primaryButton:hover {{
    background-color: {p.highlight_hover};
}}

QPushButton#primaryButton:pressed {{
    background-color: {p.highlight_pressed};
}}

QPushButton#settingsButton, QPushButton#aboutButton {{
    background-color: {p.background_secondary};
    border: 1px solid {p.border};
    border-radius: 0px;
    color: {p.text_primary};
    padding: 5px 8px;
    min-width: 24px;
    max-width: 24px;
    min-height: 0;
}}

QPushButton#settingsButton:hover, QPushButton#aboutButton:hover {{
    background-color: {p.background_hover};
    border-color: {p.border_emphasis};
}}

QPushButton#settingsButton:pressed, QPushButton#aboutButton:pressed {{
    background-color: {p.background_row_stripe};
}}

QPushButton#historyDeleteButton {{
    background-color: transparent;
    border: none;
    color: {p.text_secondary};
    padding: 2px 6px;
    min-width: 22px;
    max-width: 22px;
    border-radius: 0px;
}}

QPushButton#historyDeleteButton:hover {{
    background-color: {p.status_error};
    color: {p.highlight_text};
}}

QLineEdit, QPlainTextEdit {{
    background-color: {p.background_primary};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 0px;
    padding: 4px 4px;
    selection-background-color: {p.highlight};
    selection-color: {p.highlight_text};
}}

QLineEdit#SessionFilterEdit {{
    font-size: {filter_edit_font_size}px;
    padding: {filter_edit_pad_y}px 4px;
}}

QWidget#filePanelToolbar QLabel,
QWidget#filePanelToolbar QPushButton {{
    font-size: {file_panel_toolbar_font_size}px;
}}

QWidget#filePanelToolbar QLineEdit {{
    font-size: {file_panel_toolbar_font_size}px;
    padding: {file_panel_toolbar_pad_y}px 4px;
}}

QWidget#fileNavToolbar QToolButton#filePanelNavButton {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
    margin: 0px;
    font-size: {file_panel_toolbar_font_size}px;
}}

QWidget#fileNavToolbar QToolButton#filePanelNavButton:hover {{
    background-color: {p.background_hover};
}}

QWidget#fileNavToolbar QToolButton#filePanelNavButton:pressed {{
    background-color: {p.border};
}}

QWidget#filePanelStatusBar {{
    background-color: transparent;
}}

QWidget#filePanelStatusBar QLabel {{
    color: {p.text_secondary};
    font-size: {file_panel_statusbar_font_size}px;
}}

QWidget#filePanelStatusBar QLineEdit#fileFilterEdit {{
    font-size: {file_panel_statusbar_font_size}px;
    padding: 0px 4px;
}}

QWidget#filePanelStatusBar QToolButton#filePanelStatusButton {{
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 0px;
    margin: 0px;
    color: {p.text_primary};
    font-size: {file_panel_statusbar_font_size}px;
}}

QComboBox, QSpinBox {{
    background-color: {p.background_primary};
    color: {p.text_primary};
    border: 1px solid {p.border};
    border-radius: 0px;
    padding: 4px 8px;
    font-family: "{ui_font_family}";
    font-size: {ui_size_px}px;
    selection-background-color: {p.highlight};
    selection-color: {p.highlight_text};
}}

QComboBox {{
    padding-right: 4px;
}}

QSpinBox {{
    padding-right: 2px;
}}

QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {p.highlight};
}}

QPlainTextEdit#bodyTextEdit {{
    font-family: {terminal_font_family_css_value};
    font-size: {resolved_terminal_font_size}px;
}}

TerminalVTWidget#terminalVTWidget {{
    font-family: {terminal_font_family_css_value};
    font-size: {resolved_terminal_font_size}px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 26px;
    border: none;
    border-left: 1px solid {p.border};
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    background-color: {p.background_secondary};
}}

QComboBox::drop-down:hover {{
    background-color: {p.background_hover};
}}

QComboBox::down-arrow {{
    image: none;
    width: 14px;
    height: 14px;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: padding;
    width: 20px;
    border: none;
    border-left: 1px solid {p.border};
    border-radius: 0px;
    background-color: {p.background_secondary};
}}

QSpinBox::up-button {{
    subcontrol-position: top right;
    border-bottom: 1px solid {p.border};
    border-top-right-radius: 0px;
}}

QSpinBox::up-button:hover {{
    background-color: {p.background_hover};
}}

QSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 0px;
}}

QSpinBox::down-button:hover {{
    background-color: {p.background_hover};
}}

QComboBox QAbstractItemView, QListView#comboPopupListView {{
    background-color: {p.background_primary};
    border: 1px solid {p.border};
    selection-background-color: {p.background_secondary};
    selection-color: {p.text_primary};
    font-family: "{ui_font_family}";
    font-size: {ui_size_px}px;
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    min-height: {max(ui_size_px + 8, 22)}px;
    padding: 4px 8px;
}}

QTableWidget, QTableView {{
    background-color: {p.background_primary};
    alternate-background-color: {p.background_row_stripe};
    gridline-color: {p.table_grid};
    border: 1px solid {p.border};
    border-radius: 0px;
    selection-background-color: {p.table_selected_background};
    selection-color: {p.text_primary};
    outline: none;
}}


QTreeWidget {{
    background-color: {p.background_primary};
    alternate-background-color: {p.tree_row_stripe_background};
    border: 1px solid {p.border};
    border-radius: 0px;
    selection-background-color: {p.tree_selected_background};
    selection-color: {p.text_primary};
    outline: none;
}}

QTreeWidget::item {{
    padding: 2px 4px;
}}

QTreeWidget#SessionTree::item {{
    height: {session_tree_row_height}px;
    padding: 0px 4px;
}}

QTableWidget::item {{
    padding: 2px 4px;
    border: none;
    outline: none;
}}

QTableWidget::item:selected,
QTableWidget::item:selected:!active {{
    padding: 2px 4px;
    border: none;
    outline: none;
    background-color: {p.table_selected_background};
    color: {p.text_primary};
}}

QTableWidget#headerTable::item {{
    padding: 1px 4px;
    border: none;
    outline: none;
}}

QTableWidget#headerTable::item:selected,
QTableWidget#headerTable::item:selected:!active {{
    padding: 1px 4px;
    border: none;
    outline: none;
    background-color: {p.table_selected_background};
    color: {p.text_primary};
}}

QTableWidget QLineEdit#tableCellEditor {{
    padding: 1px 4px;
    margin: 0px;
    border: none;
    background-color: {p.background_primary};
    selection-background-color: {p.highlight};
    selection-color: {p.highlight_text};
}}

QHeaderView::section {{
    background-color: {p.background_secondary};
    color: {p.text_heading};
    border: none;
    border-bottom: 1px solid {p.border};
    border-right: 1px solid {p.border};
    padding: 2px 6px;
    margin: 0;
}}

QHeaderView::horizontal {{
    min-height: 24px;
    max-height: 24px;
}}

QListWidget {{
    background-color: {p.background_primary};
    border: 1px solid {p.border};
    border-radius: 0px;
    outline: none;
}}

QListWidget::item {{
    border-bottom: 1px solid {p.table_grid};
}}

QListWidget::item:selected,
QListWidget::item:selected:!active {{
    background-color: {p.tree_selected_background};
    color: {p.text_primary};
}}

QListWidget::item:selected:hover,
QListWidget::item:selected:!active:hover {{
    background-color: {p.tree_selected_background};
    color: {p.text_primary};
}}

QListWidget::item:hover {{
    background-color: {p.background_menu_hover};
}}

QScrollBar:vertical {{
    background: {p.background_secondary};
    width: 10px;
    border-radius: 0px;
}}

QScrollBar::handle:vertical {{
    background: {p.border};
    border-radius: 0px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: {p.border_emphasis};
}}

QScrollBar:horizontal {{
    background: {p.background_secondary};
    height: 10px;
    border-radius: 0px;
}}

QScrollBar::handle:horizontal {{
    background: {p.border};
    border-radius: 0px;
    min-width: 24px;
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}

QPushButton#checkMarkToggle {{
    background-color: {p.background_primary};
    border: 1px solid {p.border};
    border-radius: 0px;
    padding: 0;
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    max-height: 18px;
}}

QPushButton#checkMarkToggle:hover {{
    border-color: {p.border_emphasis};
    background-color: {p.background_toggle_hover};
}}

QPushButton#checkMarkToggle:checked {{
    background-color: {p.highlight};
    border-color: {p.highlight};
    color: {p.highlight_text};
}}

QPushButton#checkMarkToggle:checked:hover {{
    background-color: {p.highlight_hover};
    border-color: {p.highlight_hover};
}}

QCheckBox#sslVerifyCheck {{
    spacing: 4px;
}}

QCheckBox#sslVerifyCheck::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {p.border};
    border-radius: 0px;
    background-color: {p.background_primary};
}}

QCheckBox#sslVerifyCheck::indicator:hover {{
    border-color: {p.border_emphasis};
    background-color: {p.background_toggle_hover};
}}

QCheckBox#sslVerifyCheck::indicator:checked {{
    image: none;
    background-color: {p.highlight};
    border-color: {p.highlight};
}}

QCheckBox#sslVerifyCheck::indicator:checked:hover {{
    background-color: {p.highlight_hover};
    border-color: {p.highlight_hover};
}}

QPushButton#checkMarkToggleCompact {{
    background-color: {p.background_primary};
    border: 1px solid {p.border};
    border-radius: 0px;
    padding: 0;
    min-width: 16px;
    max-width: 16px;
    min-height: 16px;
    max-height: 16px;
}}

QPushButton#checkMarkToggleCompact:hover {{
    border-color: {p.border_emphasis};
    background-color: {p.background_toggle_hover};
}}

QPushButton#checkMarkToggleCompact:checked {{
    background-color: {p.highlight};
    border-color: {p.highlight};
    color: {p.highlight_text};
}}

QPushButton#checkMarkToggleCompact:checked:hover {{
    background-color: {p.highlight_hover};
    border-color: {p.highlight_hover};
}}

QRadioButton {{
    spacing: 6px;
}}

QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {p.border};
    border-radius: 0px;
    background: {p.background_primary};
}}

QRadioButton::indicator:checked {{
    background: {p.highlight};
    border-color: {p.highlight};
}}

QLabel#panelTitle {{
    font-weight: bold;
    color: {p.text_heading};
    padding: 4px 0;
}}

QToolButton#drawerHeaderButton {{
    border: 0;
    margin: 0;
    padding: 2px 2px;
    color: {p.text_heading};
    background: transparent;
    text-align: left;
}}

QToolButton#drawerHeaderButton:hover {{
    color: {p.highlight};
}}

QToolButton#drawerHeaderButton QLabel {{
    padding: 0;
    margin: 0;
}}

QLabel#historyItemLabel {{
    color: {p.text_primary};
    background: transparent;
}}

QLabel#statusOk {{
    padding: 0;
    margin: 0;
    color: {p.status_success};
    font-size: {appearance.status_font_size_px}px;
    font-weight: normal;
}}

QLabel#statusWarn {{
    padding: 0;
    margin: 0;
    color: {p.status_warning};
    font-size: {appearance.status_font_size_px}px;
    font-weight: normal;
}}

QLabel#statusError {{
    padding: 0;
    margin: 0;
    color: {p.status_error};
    font-size: {appearance.status_font_size_px}px;
    font-weight: normal;
}}

QLabel#statusPending {{
    padding: 0;
    margin: 0;
    color: {p.status_pending};
    font-size: {appearance.status_font_size_px}px;
    font-weight: normal;
}}
'''
