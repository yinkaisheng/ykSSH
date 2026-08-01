#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from i18n.translator import DEFAULT_LOCALE, list_languages
from log_util import logger
from storage.paths import CONFIG_FILE
from ui.appearance_defaults import (
    DEFAULT_BODY_TEXT_FONT_FAMILIES,
    DEFAULT_BODY_TEXT_FONT_FAMILY,
    DEFAULT_BODY_TEXT_FONT_FALLBACKS,
    DEFAULT_THEME,
    DEFAULT_UI_FONT_FAMILIES_WIN,
    _APPEARANCE_INT_BOUNDS,
    _APPEARANCE_INT_DEFAULTS,
    default_appearance,
)
from ui.file_panel_defaults import (
    DEFAULT_LOCAL_COLUMN_WIDTHS,
    DEFAULT_REMOTE_COLUMN_WIDTHS,
    _FILE_PANEL_INT_BOUNDS,
    _FILE_PANEL_INT_DEFAULTS,
    default_file_panel,
)
from ui.theme_defaults import DEFAULT_THEMES, DEFAULT_THEME_NAMES, merge_theme_colors

CONFIG_VERSION = 1

_VALID_THEMES = frozenset(DEFAULT_THEME_NAMES)

_TERMINAL_SETTING_DEFAULTS: Dict[str, Any] = {
    'terminal_scrollback_lines': 5000,
    'terminal_reflow_buffer_chars': 200000,
    'terminal_experimental_raw_reflow_on_resize': False,
    'terminal_paste_confirm_multiline': True,
    'terminal_bracketed_paste': True,
}


@dataclass(frozen=True)
class AppearanceConfig:
    theme: str
    ui_font_size_px: int
    table_font_size_px: int
    status_font_size_px: int
    tab_close_font_size_px: int
    ui_font_families_win: Tuple[str, ...]
    body_text_font_family: str
    body_text_font_size_px: int
    body_text_font_size_min: int
    body_text_font_size_max: int
    body_text_font_families: Tuple[str, ...]
    body_text_font_fallbacks: Tuple[str, ...]


DEFAULT_WINDOW_BORDER_WIDTH = 2
_MAX_WINDOW_BORDER_WIDTH = 8
DEFAULT_TITLE_BAR_HEIGHT = 32
_MIN_TITLE_BAR_HEIGHT = 24
_MAX_TITLE_BAR_HEIGHT = 48


@dataclass(frozen=True)
class WindowConfig:
    border_width: int = DEFAULT_WINDOW_BORDER_WIDTH
    title_bar_height: int = DEFAULT_TITLE_BAR_HEIGHT
    width: Optional[int] = None
    height: Optional[int] = None
    main_splitter: Optional[float] = None
    vertical_splitter: Optional[float] = None


@dataclass(frozen=True)
class FilePanelConfig:
    local_column_widths: Tuple[int, ...]
    remote_column_widths: Tuple[int, ...]
    header_height_px: int
    row_height_px: int


@dataclass(frozen=True)
class AppConfig:
    language: str
    themes: Dict[str, Dict[str, str]]
    appearance: AppearanceConfig
    terminal: Dict[str, Any]
    window: WindowConfig
    file_panel: FilePanelConfig


_config_cache: Optional[AppConfig] = None
_raw_config_cache: Optional[Dict[str, Any]] = None


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


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
        raw.get('body_text_font_families'),
        DEFAULT_BODY_TEXT_FONT_FAMILIES,
        strip_quotes=True,
    )
    normalized: Dict[str, Any] = {
        'theme': _normalize_theme_name(raw.get('theme')),
        'ui_font_families_win': list(_normalize_string_list(
            raw.get('ui_font_families_win'),
            DEFAULT_UI_FONT_FAMILIES_WIN,
            strip_quotes=True,
        )),
        'body_text_font_family': _normalize_font_family(
            raw.get('body_text_font_family'),
            DEFAULT_BODY_TEXT_FONT_FAMILY,
            candidates,
        ),
        'body_text_font_families': list(candidates),
        'body_text_font_fallbacks': list(_normalize_string_list(
            raw.get('body_text_font_fallbacks'),
            DEFAULT_BODY_TEXT_FONT_FALLBACKS,
            strip_quotes=True,
        )),
    }
    for key, default in _APPEARANCE_INT_DEFAULTS.items():
        minimum, maximum = _APPEARANCE_INT_BOUNDS[key]
        normalized[key] = _clamp_int(raw.get(key), default, minimum, maximum)
    if normalized['body_text_font_size_min'] > normalized['body_text_font_size_max']:
        normalized['body_text_font_size_min'] = defaults['body_text_font_size_min']
        normalized['body_text_font_size_max'] = defaults['body_text_font_size_max']
    normalized['body_text_font_size_px'] = _clamp_int(
        raw.get('body_text_font_size_px'),
        defaults['body_text_font_size_px'],
        normalized['body_text_font_size_min'],
        normalized['body_text_font_size_max'],
    )
    return normalized


def _normalize_terminal(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    normalized = dict(_TERMINAL_SETTING_DEFAULTS)
    for key, default in _TERMINAL_SETTING_DEFAULTS.items():
        if key in raw:
            normalized[key] = raw[key]
        else:
            normalized[key] = default
    return normalized


def _appearance_to_config(appearance: Dict[str, Any]) -> AppearanceConfig:
    return AppearanceConfig(
        theme=appearance['theme'],
        ui_font_size_px=appearance['ui_font_size_px'],
        table_font_size_px=appearance['table_font_size_px'],
        status_font_size_px=appearance['status_font_size_px'],
        tab_close_font_size_px=appearance['tab_close_font_size_px'],
        ui_font_families_win=tuple(appearance['ui_font_families_win']),
        body_text_font_family=appearance['body_text_font_family'],
        body_text_font_size_px=appearance['body_text_font_size_px'],
        body_text_font_size_min=appearance['body_text_font_size_min'],
        body_text_font_size_max=appearance['body_text_font_size_max'],
        body_text_font_families=tuple(appearance['body_text_font_families']),
        body_text_font_fallbacks=tuple(appearance['body_text_font_fallbacks']),
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


def _normalize_column_widths(value: Any, default: Tuple[int, ...]) -> Tuple[int, ...]:
    if not isinstance(value, list):
        return default
    widths: list[int] = []
    for item in value:
        try:
            width = int(item)
        except (TypeError, ValueError):
            continue
        widths.append(max(40, min(2000, width)))
    if len(widths) != len(default):
        return default
    return tuple(widths)


def _normalize_file_panel(raw: Any) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    normalized: Dict[str, Any] = {
        'local_column_widths': list(
            _normalize_column_widths(raw.get('local_column_widths'), DEFAULT_LOCAL_COLUMN_WIDTHS)
        ),
        'remote_column_widths': list(
            _normalize_column_widths(raw.get('remote_column_widths'), DEFAULT_REMOTE_COLUMN_WIDTHS)
        ),
    }
    for key, default in _FILE_PANEL_INT_DEFAULTS.items():
        minimum, maximum = _FILE_PANEL_INT_BOUNDS[key]
        normalized[key] = _clamp_int(raw.get(key), default, minimum, maximum)
    return normalized


def _file_panel_to_config(file_panel: Dict[str, Any]) -> FilePanelConfig:
    return FilePanelConfig(
        local_column_widths=tuple(file_panel['local_column_widths']),
        remote_column_widths=tuple(file_panel['remote_column_widths']),
        header_height_px=file_panel['header_height_px'],
        row_height_px=file_panel['row_height_px'],
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
    }
    width = raw.get('width')
    height = raw.get('height')
    main_splitter = raw.get('main_splitter')
    vertical_splitter = raw.get('vertical_splitter')
    if isinstance(width, int) and width > 0:
        normalized['width'] = width
    if isinstance(height, int) and height > 0:
        normalized['height'] = height
    if isinstance(main_splitter, (int, float)) and 0.0 <= float(main_splitter) <= 1.0:
        normalized['main_splitter'] = round(float(main_splitter), 3)
    if isinstance(vertical_splitter, (int, float)) and 0.0 <= float(vertical_splitter) <= 1.0:
        normalized['vertical_splitter'] = round(float(vertical_splitter), 3)
    return normalized


def _window_to_config(window: Dict[str, Any]) -> WindowConfig:
    return WindowConfig(
        border_width=window['border_width'],
        title_bar_height=window['title_bar_height'],
        width=window.get('width'),
        height=window.get('height'),
        main_splitter=window.get('main_splitter'),
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
    }


def _to_app_config(data: Dict[str, Any]) -> AppConfig:
    return AppConfig(
        language=data['language'],
        themes=data['themes'],
        appearance=_appearance_to_config(data['appearance']),
        terminal=dict(data.get('terminal', _TERMINAL_SETTING_DEFAULTS)),
        window=_window_to_config(data.get('window', _normalize_window({}))),
        file_panel=_file_panel_to_config(data.get('file_panel', _normalize_file_panel({}))),
    )


def _save_config(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        logger.warning(f'Failed to save app config to {path}')


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
    return False


def _load_config(path: Path = CONFIG_FILE) -> AppConfig:
    global _raw_config_cache
    if not path.exists():
        normalized = _default_config()
        _save_config(path, normalized)
        _raw_config_cache = normalized
        return _to_app_config(normalized)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning(f'Failed to load app config from {path}; using defaults')
        normalized = _default_config()
        _save_config(path, normalized)
        _raw_config_cache = normalized
        return _to_app_config(normalized)

    if not isinstance(raw, dict):
        raw = {}

    normalized = _normalize_config(raw)
    if _config_needs_save(raw, normalized):
        _save_config(path, normalized)
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
    body_text_font_family: str,
    body_text_font_size_px: int,
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
        'tab_close_font_size_px': current.tab_close_font_size_px,
        'ui_font_families_win': list(current.ui_font_families_win),
        'body_text_font_family': body_text_font_family,
        'body_text_font_size_px': body_text_font_size_px,
        'body_text_font_size_min': current.body_text_font_size_min,
        'body_text_font_size_max': current.body_text_font_size_max,
        'body_text_font_families': list(current.body_text_font_families),
        'body_text_font_fallbacks': list(current.body_text_font_fallbacks),
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
    main_splitter: float,
    vertical_splitter: float,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    window = dict(_raw_config_cache.get('window', {}))
    window['width'] = max(1, int(width))
    window['height'] = max(1, int(height))
    window['main_splitter'] = round(float(main_splitter), 3)
    window['vertical_splitter'] = round(float(vertical_splitter), 3)
    data = dict(_raw_config_cache)
    data['window'] = _normalize_window(window)
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache


def save_file_panel_column_widths(
    *,
    local_column_widths: Optional[Tuple[int, ...]] = None,
    remote_column_widths: Optional[Tuple[int, ...]] = None,
    path: Path = CONFIG_FILE,
) -> AppConfig:
    global _config_cache, _raw_config_cache
    if _raw_config_cache is None:
        _raw_config_cache = _normalize_config(_default_config())
    file_panel = dict(_raw_config_cache.get('file_panel', _normalize_file_panel({})))
    if local_column_widths is not None:
        file_panel['local_column_widths'] = list(
            _normalize_column_widths(list(local_column_widths), DEFAULT_LOCAL_COLUMN_WIDTHS)
        )
    if remote_column_widths is not None:
        file_panel['remote_column_widths'] = list(
            _normalize_column_widths(list(remote_column_widths), DEFAULT_REMOTE_COLUMN_WIDTHS)
        )
    file_panel = _normalize_file_panel(file_panel)
    data = dict(_raw_config_cache)
    data['file_panel'] = file_panel
    _save_config(path, data)
    _raw_config_cache = data
    _config_cache = _to_app_config(data)
    return _config_cache
