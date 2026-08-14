#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from i18n.translator import DEFAULT_LOCALE, list_languages
from log_util import logger
from storage.paths import CONFIG_FILE
from storage.json_io import atomic_write_json
from storage.appearance_defaults import (
    DEFAULT_TERMINAL_FONT_FAMILIES,
    DEFAULT_TERMINAL_FONT_FAMILY,
    DEFAULT_TERMINAL_FONT_FALLBACKS,
    DEFAULT_THEME,
    DEFAULT_UI_FONT_FAMILIES_WIN,
    _APPEARANCE_INT_BOUNDS,
    _APPEARANCE_INT_DEFAULTS,
    default_appearance,
)
from models.app_config import (
    AppConfig,
    AppearanceConfig,
    FilePanelConfig,
    SidePanelConfig,
    WindowConfig,
)
from models.favorite_path import FavoritePath, favorite_paths_from_raw, favorite_paths_to_raw
from storage.file_panel_defaults import (
    DEFAULT_LOCAL_COLUMN_WIDTHS,
    DEFAULT_REMOTE_COLUMN_WIDTHS,
    FILE_TABLE_COLUMNS,
    _FILE_PANEL_BOOL_DEFAULTS,
    _FILE_PANEL_INT_BOUNDS,
    _FILE_PANEL_INT_DEFAULTS,
    clamp_column_width,
    default_file_panel,
)
from storage.side_panel_defaults import (
    _SIDE_PANEL_INT_BOUNDS,
    _SIDE_PANEL_INT_DEFAULTS,
)
from storage.theme_defaults import DEFAULT_THEMES, DEFAULT_THEME_NAMES, merge_theme_colors

CONFIG_VERSION = 1

_VALID_THEMES = frozenset(DEFAULT_THEME_NAMES)

_TERMINAL_SETTING_DEFAULTS: Dict[str, Any] = {
    'terminal_scrollback_lines': 5000,
    'terminal_reflow_buffer_chars': 200000,
    'terminal_experimental_raw_reflow_on_resize': False,
    'terminal_paste_confirm_multiline': True,
    'terminal_bracketed_paste': True,
    'terminal_background_color': '#1E1E1E',
    'terminal_selection_background_color': '#094771',
    'terminal_left_gutter_width_px': 16,
    'terminal_gutter_background_color': '#252525',
    'terminal_scrollbar_width_px': 10,
    'terminal_scrollbar_background_color': '#252525',
    'terminal_scrollbar_thumb_color': '#6A6A6A',
    'terminal_debug_gutter_selection': False,
    'terminal_debug_history_jump': False,
}


DEFAULT_WINDOW_BORDER_WIDTH = 1
_MAX_WINDOW_BORDER_WIDTH = 8
DEFAULT_TITLE_BAR_HEIGHT = 32
_MIN_TITLE_BAR_HEIGHT = 24
_MAX_TITLE_BAR_HEIGHT = 48
DEFAULT_TAB_BAR_HEIGHT = 28
_MIN_TAB_BAR_HEIGHT = 20
_MAX_TAB_BAR_HEIGHT = 48
DEFAULT_SESSION_TREE_WIDTH = 280
_MIN_SESSION_TREE_WIDTH = 120
_MAX_SESSION_TREE_WIDTH = 800


_config_cache: Optional[AppConfig] = None
_raw_config_cache: Optional[Dict[str, Any]] = None


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _normalize_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in ('true', '1', 'yes', 'on'):
            return True
        if lowered in ('false', '0', 'no', 'off'):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _normalize_string_list(
    value: Any,
    default: Tuple[str, ...],
    *,
    strip_quotes: bool = False,
) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return default
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if strip_quotes:
            text = text.replace('"', '')
        if text and text not in items:
            items.append(text)
    return tuple(items) if items else default


def _normalize_theme_name(value: Any) -> str:
    if isinstance(value, str) and value in _VALID_THEMES:
        return value
    return DEFAULT_THEME


def _normalize_font_family(value: Any, default: str, candidates: Tuple[str, ...]) -> str:
    if isinstance(value, str):
        name = value.strip().replace('"', '')
        if name in candidates:
            return name
        if name:
            return name
    return default


def _normalize_appearance(raw: Any) -> Dict[str, Any]:
    defaults = default_appearance()
    raw = raw if isinstance(raw, dict) else {}
    candidates = _normalize_string_list(
        raw.get('terminal_font_families'),
        DEFAULT_TERMINAL_FONT_FAMILIES,
        strip_quotes=True,
    )
    normalized: Dict[str, Any] = {
        'theme': _normalize_theme_name(raw.get('theme')),
        'ui_font_families_win': list(_normalize_string_list(
            raw.get('ui_font_families_win'),
            DEFAULT_UI_FONT_FAMILIES_WIN,
            strip_quotes=True,
        )),
        'terminal_font_family': _normalize_font_family(
            raw.get('terminal_font_family'),
            DEFAULT_TERMINAL_FONT_FAMILY,
            candidates,
        ),
        'terminal_font_families': list(candidates),
        'terminal_font_fallbacks': list(_normalize_string_list(
            raw.get('terminal_font_fallbacks'),
            DEFAULT_TERMINAL_FONT_FALLBACKS,
            strip_quotes=True,
        )),
    }
    for key, default in _APPEARANCE_INT_DEFAULTS.items():
        minimum, maximum = _APPEARANCE_INT_BOUNDS[key]
        normalized[key] = _clamp_int(raw.get(key), default, minimum, maximum)
    if normalized['terminal_font_size_min'] > normalized['terminal_font_size_max']:
        normalized['terminal_font_size_min'] = defaults['terminal_font_size_min']
        normalized['terminal_font_size_max'] = defaults['terminal_font_size_max']
    normalized['terminal_font_size_px'] = _clamp_int(
        raw.get('terminal_font_size_px'),
        defaults['terminal_font_size_px'],
        normalized['terminal_font_size_min'],
        normalized['terminal_font_size_max'],
    )
    return normalized


def _normalize_terminal(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    normalized: Dict[str, Any] = {}
    for key, default in _TERMINAL_SETTING_DEFAULTS.items():
        if isinstance(default, bool):
            normalized[key] = _normalize_bool(raw.get(key, default), default)
        elif isinstance(default, int):
            if key in ('terminal_left_gutter_width_px', 'terminal_scrollbar_width_px'):
                minimum = 0
                maximum = 200
            else:
                minimum = 1
                maximum = 10_000_000
            normalized[key] = _clamp_int(raw.get(key, default), default, minimum, maximum)
        else:
            normalized[key] = raw.get(key, default)
    return normalized


def _appearance_to_config(appearance: Dict[str, Any]) -> AppearanceConfig:
    return AppearanceConfig(
        theme=appearance['theme'],
        ui_font_size_px=appearance['ui_font_size_px'],
        table_font_size_px=appearance['table_font_size_px'],
        status_font_size_px=appearance['status_font_size_px'],
        session_tree_font_size_px=appearance['session_tree_font_size_px'],
        session_tree_row_height_px=appearance['session_tree_row_height_px'],
        filter_edit_height=appearance['filter_edit_height'],
        filter_edit_font_size=appearance['filter_edit_font_size'],
        ui_font_families_win=tuple(appearance['ui_font_families_win']),
        terminal_font_family=appearance['terminal_font_family'],
        terminal_font_size_px=appearance['terminal_font_size_px'],
        terminal_font_size_min=appearance['terminal_font_size_min'],
        terminal_font_size_max=appearance['terminal_font_size_max'],
        terminal_font_families=tuple(appearance['terminal_font_families']),
        terminal_font_fallbacks=tuple(appearance['terminal_font_fallbacks']),
    )


def _default_themes() -> Dict[str, Dict[str, str]]:
    return {name: dict(DEFAULT_THEMES[name]) for name in DEFAULT_THEME_NAMES}


def _normalize_themes(raw_themes: Any) -> Dict[str, Dict[str, str]]:
    if not isinstance(raw_themes, dict):
        raw_themes = {}
    normalized: Dict[str, Dict[str, str]] = {}
    for theme_name in DEFAULT_THEME_NAMES:
        theme_raw = raw_themes.get(theme_name, {})
        if not isinstance(theme_raw, dict):
            theme_raw = {}
        normalized[theme_name] = merge_theme_colors(theme_name, theme_raw)
    return normalized


def _normalize_language(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return DEFAULT_LOCALE
    code = value.strip()
    available = {info.code for info in list_languages()}
    return code if code in available else DEFAULT_LOCALE


def _normalize_column_widths(
    value: Any,
    *,
    default: Dict[str, int],
    columns: Tuple[str, ...] = FILE_TABLE_COLUMNS,
) -> Dict[str, int]:
    if isinstance(value, dict):
        normalized: Dict[str, int] = {}
        for key in columns:
            if key in value:
                normalized[key] = clamp_column_width(value[key], default.get(key, 100))
            elif key in default:
                normalized[key] = default[key]
        for key, raw_width in value.items():
            if key not in normalized:
                normalized[key] = clamp_column_width(raw_width, default.get(key, 100))
        return normalized

    return dict(default)


def _normalize_favorite_entries(raw: Any) -> list[dict[str, str]]:
    return favorite_paths_to_raw(favorite_paths_from_raw(raw))


def _normalize_file_panel(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    normalized: Dict[str, Any] = {
        'local_column_widths': _normalize_column_widths(
            raw.get('local_column_widths'),
            default=DEFAULT_LOCAL_COLUMN_WIDTHS,
        ),
        'remote_column_widths': _normalize_column_widths(
            raw.get('remote_column_widths'),
            default=DEFAULT_REMOTE_COLUMN_WIDTHS,
        ),
        'local_favorites': _normalize_favorite_entries(raw.get('local_favorites')),
    }
    for key, default in _FILE_PANEL_INT_DEFAULTS.items():
        minimum, maximum = _FILE_PANEL_INT_BOUNDS[key]
        normalized[key] = _clamp_int(raw.get(key), default, minimum, maximum)
    for key, default in _FILE_PANEL_BOOL_DEFAULTS.items():
        normalized[key] = _normalize_bool(raw.get(key), default)
    return normalized


def _file_panel_to_config(file_panel: Dict[str, Any]) -> FilePanelConfig:
    return FilePanelConfig(
        local_column_widths=dict(file_panel['local_column_widths']),
        remote_column_widths=dict(file_panel['remote_column_widths']),
        header_height_px=file_panel['header_height_px'],
        row_height_px=file_panel['row_height_px'],
        file_panel_toolbar_height=file_panel['file_panel_toolbar_height'],
        file_panel_toolbar_font_size=file_panel['file_panel_toolbar_font_size'],
        file_panel_statusbar_font_size=file_panel['file_panel_statusbar_font_size'],
        file_panel_favorites_menu_font_size=file_panel['file_panel_favorites_menu_font_size'],
        folder_name_bold=file_panel['folder_name_bold'],
        local_favorites=tuple(favorite_paths_from_raw(file_panel.get('local_favorites'))),
        local_favorites_dialog_width=file_panel['local_favorites_dialog_width'],
        local_favorites_dialog_height=file_panel['local_favorites_dialog_height'],
        remote_favorites_dialog_width=file_panel['remote_favorites_dialog_width'],
        remote_favorites_dialog_height=file_panel['remote_favorites_dialog_height'],
    )


def _normalize_side_panel(raw: Any) -> Dict[str, int]:
    raw = raw if isinstance(raw, dict) else {}
    normalized: Dict[str, int] = {}
    for key, default in _SIDE_PANEL_INT_DEFAULTS.items():
        minimum, maximum = _SIDE_PANEL_INT_BOUNDS[key]
        normalized[key] = _clamp_int(raw.get(key), default, minimum, maximum)
    return normalized


def _side_panel_to_config(side_panel: Dict[str, int]) -> SidePanelConfig:
    return SidePanelConfig(
        session_edit_dialog_width=side_panel['session_edit_dialog_width'],
        session_edit_dialog_height=side_panel['session_edit_dialog_height'],
        command_edit_dialog_width=side_panel['command_edit_dialog_width'],
        command_edit_dialog_height=side_panel['command_edit_dialog_height'],
    )


def _normalize_window(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    normalized: Dict[str, Any] = {
        'border_width': _clamp_int(
            raw.get('border_width'),
            DEFAULT_WINDOW_BORDER_WIDTH,
            0,
            _MAX_WINDOW_BORDER_WIDTH,
        ),
        'title_bar_height': _clamp_int(
            raw.get('title_bar_height'),
            DEFAULT_TITLE_BAR_HEIGHT,
            _MIN_TITLE_BAR_HEIGHT,
            _MAX_TITLE_BAR_HEIGHT,
        ),
        'tab_bar_height': _clamp_int(
            raw.get('tab_bar_height'),
            DEFAULT_TAB_BAR_HEIGHT,
            _MIN_TAB_BAR_HEIGHT,
            _MAX_TAB_BAR_HEIGHT,
        ),
    }
    width = raw.get('width')
    height = raw.get('height')
    vertical_splitter = raw.get('vertical_splitter')
    if isinstance(width, int) and width > 0:
        normalized['width'] = width
    if isinstance(height, int) and height > 0:
        normalized['height'] = height
    normalized['session_tree_width'] = _clamp_int(
        raw.get('session_tree_width'),
        DEFAULT_SESSION_TREE_WIDTH,
        _MIN_SESSION_TREE_WIDTH,
        _MAX_SESSION_TREE_WIDTH,
    )
    if isinstance(vertical_splitter, (int, float)) and 0.0 <= float(vertical_splitter) <= 1.0:
        normalized['vertical_splitter'] = round(float(vertical_splitter), 3)
    return normalized


def _window_to_config(window: Dict[str, Any]) -> WindowConfig:
    return WindowConfig(
        border_width=window['border_width'],
        title_bar_height=window['title_bar_height'],
        tab_bar_height=window['tab_bar_height'],
        width=window.get('width'),
        height=window.get('height'),
        session_tree_width=window.get('session_tree_width'),
        vertical_splitter=window.get('vertical_splitter'),
    )


def _normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {'version': CONFIG_VERSION}
    normalized['themes'] = _normalize_themes(raw.get('themes'))
    normalized['appearance'] = _normalize_appearance(raw.get('appearance'))
    normalized['language'] = _normalize_language(raw.get('language'))
    normalized['terminal'] = _normalize_terminal(raw.get('terminal'))
    normalized['window'] = _normalize_window(raw.get('window'))
    normalized['file_panel'] = _normalize_file_panel(raw.get('file_panel'))
    normalized['side_panel'] = _normalize_side_panel(raw.get('side_panel'))
    return normalized


def _default_config() -> Dict[str, Any]:
    return {
        'version': CONFIG_VERSION,
        'themes': _default_themes(),
        'appearance': default_appearance(),
        'language': DEFAULT_LOCALE,
        'terminal': dict(_TERMINAL_SETTING_DEFAULTS),
        'window': _normalize_window({}),
        'file_panel': _normalize_file_panel({}),
        'side_panel': _normalize_side_panel({}),
    }


def _to_app_config(data: Dict[str, Any]) -> AppConfig:
    return AppConfig(
        language=data['language'],
        themes=data['themes'],
        appearance=_appearance_to_config(data['appearance']),
        terminal=dict(data.get('terminal', _TERMINAL_SETTING_DEFAULTS)),
        window=_window_to_config(data.get('window', _normalize_window({}))),
        file_panel=_file_panel_to_config(data.get('file_panel', _normalize_file_panel({}))),
        side_panel=_side_panel_to_config(data.get('side_panel', _normalize_side_panel({}))),
    )


def _save_config(path: Path, data: Dict[str, Any]) -> None:
    atomic_write_json(path, data)


def _config_needs_save(raw: Dict[str, Any], normalized: Dict[str, Any]) -> bool:
    if raw.get('version') != normalized['version']:
        return True
    raw_themes = raw.get('themes')
    if not isinstance(raw_themes, dict):
        return True
    for theme_name in DEFAULT_THEME_NAMES:
        theme_raw = raw_themes.get(theme_name)
        if not isinstance(theme_raw, dict):
            return True
        if merge_theme_colors(theme_name, theme_raw) != normalized['themes'][theme_name]:
            return True
    if raw.get('appearance') != normalized['appearance']:
        return True
    if raw.get('language') != normalized['language']:
        return True
    if raw.get('terminal') != normalized['terminal']:
        return True
    if raw.get('window') != normalized['window']:
        return True
    if raw.get('file_panel') != normalized['file_panel']:
        return True
    if raw.get('side_panel') != normalized['side_panel']:
        return True
    return False


def _load_config(path: Path = CONFIG_FILE) -> AppConfig:
    global _raw_config_cache
    if not path.exists():
        normalized = _default_config()
        try:
            _save_config(path, normalized)
        except OSError as exc:
            logger.warning(f'Failed to create app config at {path}: {exc}')
        _raw_config_cache = normalized
        return _to_app_config(normalized)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning(f'Failed to load app config from {path}; using defaults')
        normalized = _default_config()
        try:
            _save_config(path, normalized)
        except OSError as exc:
            logger.warning(f'Failed to replace invalid app config at {path}: {exc}')
        _raw_config_cache = normalized
        return _to_app_config(normalized)

    if not isinstance(raw, dict):
        raw = {}

    normalized = _normalize_config(raw)
    if _config_needs_save(raw, normalized):
        try:
            _save_config(path, normalized)
        except OSError as exc:
            logger.warning(f'Failed to normalize app config at {path}: {exc}')
    _raw_config_cache = normalized
    return _to_app_config(normalized)


def get_app_config() -> AppConfig:
    global _config_cache
    if _config_cache is None:
        _config_cache = _load_config()
    return _config_cache


def init_app_config() -> AppConfig:
    global _config_cache
    _config_cache = _load_config()
    return _config_cache


def get_setting(key: str, default: Any = None) -> Any:
    cfg = get_app_config()
    terminal = cfg.terminal
    if key in terminal:
        return terminal[key]
    if key in _TERMINAL_SETTING_DEFAULTS:
        return _TERMINAL_SETTING_DEFAULTS[key]
    return default


def save_app_preferences(
    *,
    theme: str,
    terminal_font_family: str,
    terminal_font_size_px: int,
    language: str,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    global _config_cache, _raw_config_cache
    cfg = get_app_config()
    current = cfg.appearance
    appearance = _normalize_appearance({
        'theme': theme,
        'ui_font_size_px': current.ui_font_size_px,
        'table_font_size_px': current.table_font_size_px,
        'status_font_size_px': current.status_font_size_px,
        'session_tree_font_size_px': current.session_tree_font_size_px,
        'session_tree_row_height_px': current.session_tree_row_height_px,
        'filter_edit_height': current.filter_edit_height,
        'filter_edit_font_size': current.filter_edit_font_size,
        'ui_font_families_win': list(current.ui_font_families_win),
        'terminal_font_family': terminal_font_family,
        'terminal_font_size_px': terminal_font_size_px,
        'terminal_font_size_min': current.terminal_font_size_min,
        'terminal_font_size_max': current.terminal_font_size_max,
        'terminal_font_families': list(current.terminal_font_families),
        'terminal_font_fallbacks': list(current.terminal_font_fallbacks),
    })
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    data = dict(_raw_config_cache)
    data['appearance'] = appearance
    data['language'] = _normalize_language(language)
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache


def save_window_state(
    *,
    width: int,
    height: int,
    session_tree_width: int,
    vertical_splitter: float,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    window = dict(_raw_config_cache.get('window', {}))
    window['width'] = max(1, int(width))
    window['height'] = max(1, int(height))
    window['session_tree_width'] = _clamp_int(
        session_tree_width,
        DEFAULT_SESSION_TREE_WIDTH,
        _MIN_SESSION_TREE_WIDTH,
        _MAX_SESSION_TREE_WIDTH,
    )
    window['vertical_splitter'] = round(float(vertical_splitter), 3)
    data = dict(_raw_config_cache)
    data['window'] = _normalize_window(window)
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache


def save_file_panel_column_widths(
    *,
    local_column_widths: Optional[Dict[str, int]] = None,
    remote_column_widths: Optional[Dict[str, int]] = None,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    file_panel = dict(_raw_config_cache.get('file_panel', _normalize_file_panel({})))
    if local_column_widths is not None:
        file_panel['local_column_widths'] = _normalize_column_widths(
            local_column_widths,
            default=DEFAULT_LOCAL_COLUMN_WIDTHS,
        )
    if remote_column_widths is not None:
        file_panel['remote_column_widths'] = _normalize_column_widths(
            remote_column_widths,
            default=DEFAULT_REMOTE_COLUMN_WIDTHS,
        )
    file_panel = _normalize_file_panel(file_panel)
    data = dict(_raw_config_cache)
    data['file_panel'] = file_panel
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache


def save_file_panel_local_favorites(
    favorites: Sequence[FavoritePath],
    path: Path = CONFIG_FILE,
) -> AppConfig:
    """Persist global local favorite paths under file_panel.local_favorites."""
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    file_panel = dict(_raw_config_cache.get('file_panel', _normalize_file_panel({})))
    file_panel['local_favorites'] = favorite_paths_to_raw(favorites)
    file_panel = _normalize_file_panel(file_panel)
    data = dict(_raw_config_cache)
    data['file_panel'] = file_panel
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache


def save_favorites_dialog_size(
    *,
    local: bool,
    width: int,
    height: int,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    """Persist favorites manage dialog size under file_panel.*_favorites_dialog_*."""
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    file_panel = dict(_raw_config_cache.get('file_panel', _normalize_file_panel({})))
    if local:
        file_panel['local_favorites_dialog_width'] = width
        file_panel['local_favorites_dialog_height'] = height
    else:
        file_panel['remote_favorites_dialog_width'] = width
        file_panel['remote_favorites_dialog_height'] = height
    file_panel = _normalize_file_panel(file_panel)
    data = dict(_raw_config_cache)
    data['file_panel'] = file_panel
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache


def save_session_edit_dialog_size(
    *,
    width: int,
    height: int,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    """Persist session edit dialog size under side_panel.session_edit_dialog_*."""
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    side_panel = dict(_raw_config_cache.get('side_panel', _normalize_side_panel({})))
    side_panel['session_edit_dialog_width'] = width
    side_panel['session_edit_dialog_height'] = height
    side_panel = _normalize_side_panel(side_panel)
    data = dict(_raw_config_cache)
    data['side_panel'] = side_panel
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache


def save_command_edit_dialog_size(
    *,
    width: int,
    height: int,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    """Persist command edit dialog size under side_panel.command_edit_dialog_*."""
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    side_panel = dict(_raw_config_cache.get('side_panel', _normalize_side_panel({})))
    side_panel['command_edit_dialog_width'] = width
    side_panel['command_edit_dialog_height'] = height
    side_panel = _normalize_side_panel(side_panel)
    data = dict(_raw_config_cache)
    data['side_panel'] = side_panel
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache
