#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from i18n import get_language, list_languages, register_retranslator, tr, unregister_retranslator
from storage.app_config import get_app_config
from ui.dialog_common import (
    add_form_field,
    create_dialog,
    create_form_grid,
    refresh_combo_items,
    select_combo_by_data,
)
from ui.dialog_i18n import translate_button_box
from ui.theme import (
    THEME_OPTIONS,
    ThemeName,
    body_text_font_size_max,
    body_text_font_size_min,
    normalize_body_text_font_family,
    normalize_theme_name,
)
from ui.widgets import ArrowComboBox, GlyphSpinBox


@dataclass(frozen=True)
class AppSettings:
    theme: ThemeName
    size: int
    family: str
    language: str


def _select_body_font_family(combo: ArrowComboBox, family: str) -> None:
    target = normalize_body_text_font_family(family)
    index = combo.findText(target)
    if index >= 0:
        combo.setCurrentIndex(index)
        return
    combo.setCurrentIndex(0)


def prompt_app_settings(
    parent: QWidget,
    current_theme: str,
    current_size: int,
    current_family: str,
    current_language: str,
    *,
    on_save: Optional[Callable[[AppSettings], None]] = None,
    min_width: int = 400,
) -> Optional[AppSettings]:
    dialog = create_dialog(parent, tr('settings.title'), min_width=min_width)
    initial = AppSettings(
        theme=normalize_theme_name(current_theme),
        size=current_size,
        family=normalize_body_text_font_family(current_family),
        language=current_language or get_language(),
    )
    last_saved = initial

    layout = QVBoxLayout(dialog)
    grid = create_form_grid()
    theme_combo = ArrowComboBox()
    theme_combo.setMinimumWidth(min_width - 48)
    for theme_name in THEME_OPTIONS:
        theme_combo.addItem(tr(f'theme.{theme_name}'), theme_name)
    theme_combo.setCurrentIndex(THEME_OPTIONS.index(initial.theme))
    theme_label = add_form_field(grid, 0, tr('settings.theme'), theme_combo)

    language_combo = ArrowComboBox()
    language_combo.setMinimumWidth(min_width - 48)
    for info in list_languages():
        language_combo.addItem(info.name, info.code)
    select_combo_by_data(language_combo, initial.language)
    language_label = add_form_field(grid, 1, tr('settings.language'), language_combo)

    family_combo = ArrowComboBox()
    family_combo.setMinimumWidth(min_width - 48)
    db = QFontDatabase()
    system_families = set(db.families())
    preferred = get_app_config().appearance.body_text_font_families
    seen: set = set()
    for family_name in preferred:
        if family_name not in seen and family_name in system_families:
            family_combo.addItem(family_name)
            seen.add(family_name)
    for family_name in db.families():
        if family_name not in seen and db.isFixedPitch(family_name):
            family_combo.addItem(family_name)
            seen.add(family_name)
    _select_body_font_family(family_combo, initial.family)
    family_label = add_form_field(grid, 2, tr('settings.editor_font_family'), family_combo)

    spin = GlyphSpinBox()
    spin.setRange(body_text_font_size_min(), body_text_font_size_max())
    spin.setValue(initial.size)
    spin.setMinimumWidth(120)
    size_label = add_form_field(grid, 3, tr('settings.editor_font_size'), spin)

    def current_settings() -> AppSettings:
        return AppSettings(
            theme=normalize_theme_name(theme_combo.currentData()),
            size=spin.value(),
            family=normalize_body_text_font_family(family_combo.currentText()),
            language=language_combo.currentData(),
        )

    buttons = QDialogButtonBox(
        QDialogButtonBox.Apply | QDialogButtonBox.Save | QDialogButtonBox.Close,
        parent=dialog,
    )
    translate_button_box(buttons)
    apply_btn = buttons.button(QDialogButtonBox.Apply)

    def update_apply_enabled() -> None:
        if apply_btn is not None:
            apply_btn.setEnabled(current_settings() != last_saved)

    def commit_settings(settings: AppSettings) -> None:
        nonlocal last_saved
        if settings == last_saved:
            return
        if on_save is not None:
            on_save(settings)
        last_saved = settings
        update_apply_enabled()

    def do_apply() -> None:
        commit_settings(current_settings())

    if apply_btn is not None:
        apply_btn.clicked.connect(do_apply)
        apply_btn.setEnabled(False)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addLayout(grid)
    layout.addWidget(buttons)

    theme_combo.currentIndexChanged.connect(lambda _i: update_apply_enabled())
    language_combo.currentIndexChanged.connect(lambda _i: update_apply_enabled())
    family_combo.currentIndexChanged.connect(lambda _i: update_apply_enabled())
    spin.valueChanged.connect(lambda _v: update_apply_enabled())

    def retranslate_settings_dialog() -> None:
        dialog.setWindowTitle(tr('settings.title'))
        theme_label.setText(tr('settings.theme'))
        language_label.setText(tr('settings.language'))
        family_label.setText(tr('settings.editor_font_family'))
        size_label.setText(tr('settings.editor_font_size'))
        refresh_combo_items(
            theme_combo,
            [(tr(f'theme.{theme_name}'), theme_name) for theme_name in THEME_OPTIONS],
        )
        refresh_combo_items(
            language_combo,
            [(info.name, info.code) for info in list_languages()],
        )
        translate_button_box(buttons)

    register_retranslator(retranslate_settings_dialog)
    dialog.finished.connect(lambda _result: unregister_retranslator(retranslate_settings_dialog))

    theme_combo.setFocus()

    if dialog.exec_() != QDialog.Accepted:
        return None
    commit_settings(current_settings())
    return last_saved
