#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QSS template assembly for application themes."""
from __future__ import annotations

from typing import Any


def build_stylesheet(
    *,
    appearance: Any,
    p: Any,
    ui_size_px: int,
    tab_bar_height: int,
    tab_padding_y: int,
    session_tree_row_height: int,
    filter_edit_font_size: int,
    filter_edit_pad_y: int,
    file_panel_toolbar_font_size: int,
    file_panel_toolbar_pad_y: int,
    file_panel_statusbar_font_size: int,
    resolved_terminal_font_size: int,
    terminal_font_family_css_value: str,
    ui_font_family: str,
) -> str:
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
