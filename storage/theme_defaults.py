#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default theme colors and field documentation for config.json themes.*."""
from __future__ import annotations

import re


ThemeColorDict = dict[str, str]

# Single source of truth for config.json → themes.<solarized|light|dark>.<key>
THEME_COLOR_DOCS: dict[str, str] = {
    'background_primary': 'Main window, editors, and primary content background',
    'text_primary': 'Default body text',
    'background_secondary': 'Buttons, menu bar, table headers, combo/spin controls',
    'background_row_stripe': 'Alternating stripe background in tables',
    'border': 'Default control and panel borders',
    'border_emphasis': 'Stronger border on hover, focus, and selected tabs',
    'text_heading': 'Section titles, panel headings, status bar text',
    'text_secondary': 'Inactive tabs, subtle labels, tab close buttons',
    'text_disabled': 'Disabled menu items and controls',
    'background_hover': 'Generic hover background (buttons, scrollbars, splitters)',
    'background_menu_hover': 'List item hover/selection background',
    'side_panel_item_hover_background': 'Hovered items in SidePanel session, command, and history views',
    'background_toggle_hover': 'Unchecked checkbox/toggle hover background',
    'highlight': 'Primary actions: Send button, checked boxes/radios, text selection',
    'highlight_hover': 'Hover state for highlight controls',
    'highlight_pressed': 'Pressed state for highlight controls',
    'highlight_text': 'Text and icons drawn on highlight backgrounds',
    'table_selected_background': 'Selected row background in header/response tables',
    'table_inactive_selected_background': 'Selected rows in an unfocused file table',
    'tree_row_stripe_background': 'Alternating stripe background in JSON Tree',
    'tree_selected_background': 'Selected row background in JSON Tree',
    'tab_selected_background': 'Active request tab background',
    'tab_background': 'Inactive request tab background',
    'tab_hover_background': 'Inactive request tab hover background',
    'link': 'Hyperlink text color (About dialog, etc.)',
    'status_error': 'HTTP error status text; destructive hover accents',
    'status_success': 'HTTP 2xx status text',
    'status_warning': 'HTTP 4xx status text',
    'status_pending': 'Waiting / idle status text',
    'table_grid': 'Table grid lines and list dividers',
    'title_bar_background': 'Custom title bar background',
    'window_border': 'Frameless main window outer border color',
    'terminal_focus_border': '1px border on the terminal widget while it has focus',
}

THEME_COLOR_KEY_ORDER = tuple(THEME_COLOR_DOCS.keys())


def format_theme_palette_doc() -> str:
    """Build ThemePalette class docstring from THEME_COLOR_DOCS."""
    lines = [
        'Runtime palette from config.json themes.<solarized|light|dark>.',
        '',
        'Fields:',
    ]
    for key, description in THEME_COLOR_DOCS.items():
        lines.append(f'  {key} — {description}')
    return '\n'.join(lines)


_HEX_COLOR_RE = re.compile(r'^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$')


def is_valid_theme_color(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_COLOR_RE.match(value.strip()))


def normalize_theme_color(value: object, default: str) -> str:
    if is_valid_theme_color(value):
        return value.strip()
    return default


def merge_theme_colors(theme_name: str, overrides: dict[str, object]) -> ThemeColorDict:
    defaults = DEFAULT_THEMES[theme_name]
    merged: ThemeColorDict = {}
    for key in THEME_COLOR_KEY_ORDER:
        merged[key] = normalize_theme_color(overrides.get(key), defaults[key])
    return merged


DEFAULT_THEMES: dict[str, ThemeColorDict] = {
    'solarized': {
        'background_primary': '#fdf6e3',
        'text_primary': '#657b83',
        'background_secondary': '#eee8d5',
        'background_row_stripe': '#faf4e6',
        'border': '#93a2a1',
        'border_emphasis': '#839496',
        'text_heading': '#586e75',
        'text_secondary': '#839496',
        'text_disabled': '#93a2a1',
        'background_hover': '#e8e2cf',
        'background_menu_hover': '#d8cfad',
        'side_panel_item_hover_background': '#e8e2cf',
        'background_toggle_hover': '#faf4e6',
        'highlight': '#268bd2',
        'highlight_hover': '#2f9ee0',
        'highlight_pressed': '#1f7bb8',
        'highlight_text': '#fdf6e3',
        'table_selected_background': '#d8cfad',
        'table_inactive_selected_background': "#ebe3ca",
        'tree_row_stripe_background': '#eee8d5',
        'tree_selected_background': '#d5cfb0',
        'tab_selected_background': '#fdf6e3',
        'tab_background': '#e0dac8',
        'tab_hover_background': '#e8e2d2',
        'link': '#268bd2',
        'status_error': '#dc322f',
        'status_success': '#859900',
        'status_warning': '#cb4b16',
        'status_pending': '#586e75',
        'table_grid': '#eee8d5',
        'title_bar_background': '#e0dac8',
        'window_border': '#839496',
        'terminal_focus_border': '#cb4b16',
    },
    'light': {
        'background_primary': '#f5f5f5',
        'text_primary': '#333333',
        'background_secondary': '#f0f0f0',
        'background_row_stripe': '#ececec',
        'border': '#e0e0e0',
        'border_emphasis': '#c8c8c8',
        'text_heading': '#555555',
        'text_secondary': '#777777',
        'text_disabled': '#aaaaaa',
        'background_hover': '#eeeeee',
        'background_menu_hover': '#b8d0f5',
        'side_panel_item_hover_background': '#eeeeee',
        'background_toggle_hover': '#f0f0f0',
        'highlight': '#268bd2',
        'highlight_hover': '#2f9ee0',
        'highlight_pressed': '#1f7bb8',
        'highlight_text': '#ffffff',
        'table_selected_background': '#b8d0f5',
        'table_inactive_selected_background': '#d0e4f7',
        'tree_row_stripe_background': '#f0f0f0',
        'tree_selected_background': '#d8d8d8',
        'tab_selected_background': '#f5f5f5',
        'tab_background': '#c8c8c8',
        'tab_hover_background': '#e0e0e0',
        'link': '#1a6fb5',
        'status_error': '#dc322f',
        'status_success': '#859900',
        'status_warning': '#cb4b16',
        'status_pending': '#666666',
        'table_grid': '#eeeeee',
        'title_bar_background': '#dcdcdc',
        'window_border': '#c8c8c8',
        'terminal_focus_border': '#e67e22',
    },
    'dark': {
        'background_primary': '#2d2d2d',
        'text_primary': '#d4d4d4',
        'background_secondary': '#383838',
        'background_row_stripe': '#333333',
        'border': '#505050',
        'border_emphasis': '#606060',
        'text_heading': '#e0e0e0',
        'text_secondary': '#a0a0a0',
        'text_disabled': '#707070',
        'background_hover': '#454545',
        'background_menu_hover': '#094771',
        'side_panel_item_hover_background': '#454545',
        'background_toggle_hover': '#3a3a3a',
        'highlight': '#268bd2',
        'highlight_hover': '#2f9ee0',
        'highlight_pressed': '#1f7bb8',
        'highlight_text': '#ffffff',
        'table_selected_background': '#094771',
        'table_inactive_selected_background': '#31556b',
        'tree_row_stripe_background': '#333333',
        'tree_selected_background': '#505050',
        'tab_selected_background': '#5f5f5f',
        'tab_background': '#333333',
        'tab_hover_background': '#404040',
        'link': '#4daafc',
        'status_error': '#f14c4c',
        'status_success': '#89d185',
        'status_warning': '#cca700',
        'status_pending': '#a0a0a0',
        'table_grid': '#404040',
        'title_bar_background': '#383838',
        'window_border': '#505050',
        'terminal_focus_border': '#f0a030',
    },
}

DEFAULT_THEME_NAMES = tuple(DEFAULT_THEMES.keys())
