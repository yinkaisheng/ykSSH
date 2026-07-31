#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI translation loader.

Language packs live under ``Languages/`` next to ``main.py`` (see ``storage.paths.LANGUAGES_DIR``).
Each language is a subdirectory named with a locale code. Common folder names:

  en          English
  zh-CN       Simplified Chinese (简体中文)
  zh-TW       Traditional Chinese (繁體中文)
  ja          Japanese (日本語)
  ko          Korean (한국어)
  de          German (Deutsch)
  fr          French (Français)
  es          Spanish (Español)
  pt-BR       Portuguese (Brazil)
  ru          Russian (Русский)

Required files per language directory:

  language.ini   display name for Settings (see Languages/en/language.ini)
  strings.txt    UTF-8 plain text, one ``key=value`` per line (# comments allowed)

  Literal braces in text (no placeholders): write ``{Key}`` as-is.
  Only escape as ``{{Key}}`` when the string uses ``tr('key', name=...)`` and
  you need a literal ``{`` after ``.format()`` runs.

Qt built-in strings — two approaches (this project uses B, not A):

  A) **Common Qt way:** ``QApplication.installTranslator()`` + PyQt5 ``.qm`` files
     under ``Qt5/translations/`` (see ``i18n/qt_locale.py``, commented out).
  B) **This project:** ``strings.txt`` + ``ui/dialog_i18n.py`` + ``ui/widgets.py``
     so release builds need not ship PyQt5's ``translations/`` directory.
"""
from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from i18n.builtin_strings import BUILTIN_STRINGS
from log_util import logger
from storage.paths import LANGUAGES_DIR

DEFAULT_LOCALE = 'en'
LANGUAGE_META_FILE = 'language.ini'
STRINGS_FILE = 'strings.txt'


@dataclass(frozen=True)
class LanguageInfo:
    code: str
    name: str


_translator: Optional['Translator'] = None


class Translator:
    def __init__(self, locale: str = DEFAULT_LOCALE) -> None:
        self._locale = DEFAULT_LOCALE
        self._strings: Dict[str, str] = dict(BUILTIN_STRINGS)
        self._retranslators: List[Callable[[], None]] = []
        self.set_locale(locale)

    def locale(self) -> str:
        return self._locale

    def set_locale(self, locale: str) -> None:
        normalized = _normalize_locale_code(locale)
        self._locale = normalized
        self._strings = dict(BUILTIN_STRINGS)
        if normalized == DEFAULT_LOCALE:
            overlay = LANGUAGES_DIR / DEFAULT_LOCALE / STRINGS_FILE
            if overlay.is_file():
                self._merge_strings_file(overlay)
        else:
            self._load_locale_file(normalized)

    def tr(self, key: str, **kwargs: object) -> str:
        template = self._strings.get(key, BUILTIN_STRINGS.get(key, key))
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            logger.warning('i18n format failed for key=%s kwargs=%s', key, kwargs)
            return template

    def register_retranslator(self, callback: Callable[[], None]) -> None:
        if callback not in self._retranslators:
            self._retranslators.append(callback)

    def unregister_retranslator(self, callback: Callable[[], None]) -> None:
        if callback in self._retranslators:
            self._retranslators.remove(callback)

    def notify_retranslate(self) -> None:
        for callback in list(self._retranslators):
            callback()

    def _load_locale_file(self, locale: str) -> None:
        strings_path = LANGUAGES_DIR / locale / STRINGS_FILE
        if strings_path.is_file():
            self._merge_strings_file(strings_path)
        else:
            logger.warning('Missing strings file for locale %s: %s', locale, strings_path)

    def _merge_strings_file(self, path) -> None:
        for key, value in _parse_strings_file(path).items():
            self._strings[key] = value


def _normalize_locale_code(locale: str) -> str:
    text = (locale or DEFAULT_LOCALE).strip()
    return text or DEFAULT_LOCALE


def _parse_strings_file(path) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if '=' not in stripped:
            continue
        key, _, value = stripped.partition('=')
        key = key.strip()
        value = value.strip()
        if key:
            parsed[key] = value
    return parsed


def _read_language_name(code: str) -> str:
    meta_path = LANGUAGES_DIR / code / LANGUAGE_META_FILE
    if meta_path.is_file():
        parser = ConfigParser()
        parser.read(meta_path, encoding='utf-8')
        if parser.has_option('language', 'name'):
            name = parser.get('language', 'name').strip()
            if name:
                return name
    if code == DEFAULT_LOCALE:
        return BUILTIN_STRINGS.get('language.en', 'English')
    return code


def _discover_language_codes() -> List[str]:
    codes = {DEFAULT_LOCALE}
    if LANGUAGES_DIR.is_dir():
        for entry in sorted(LANGUAGES_DIR.iterdir()):
            if not entry.is_dir():
                continue
            if (entry / STRINGS_FILE).is_file():
                codes.add(entry.name)
    return sorted(codes, key=_language_sort_key)


def _language_sort_key(code: str) -> tuple:
    if code == DEFAULT_LOCALE:
        return (0, code.lower())
    return (1, code.lower())


def _get_translator() -> Translator:
    global _translator
    if _translator is None:
        _translator = Translator(DEFAULT_LOCALE)
    return _translator


def init_i18n(locale: str) -> None:
    _get_translator().set_locale(locale)


def get_language() -> str:
    return _get_translator().locale()


def set_language(locale: str) -> None:
    translator = _get_translator()
    translator.set_locale(locale)
    translator.notify_retranslate()


def tr(key: str, **kwargs: object) -> str:
    return _get_translator().tr(key, **kwargs)


def register_retranslator(callback: Callable[[], None]) -> None:
    _get_translator().register_retranslator(callback)


def unregister_retranslator(callback: Callable[[], None]) -> None:
    _get_translator().unregister_retranslator(callback)


def list_languages() -> List[LanguageInfo]:
    return [
        LanguageInfo(code=code, name=_read_language_name(code))
        for code in _discover_language_codes()
    ]
