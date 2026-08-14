#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from i18n import get_language, list_languages, register_retranslator, tr, unregister_retranslator
from storage.app_config import get_app_config
from ui.dialog_common import (
    add_form_field,
    create_dialog,
    create_form_grid,
    refresh_combo_items,
    select_combo_by_data,
)
from ui.dialog_i18n import get_open_file_name, translate_button_box
from ui.theme import (
    THEME_OPTIONS,
    ThemeName,
    terminal_font_size_max,
    terminal_font_size_min,
    normalize_terminal_font_family,
    normalize_theme_name,
)
from ui.widgets import ArrowComboBox, GlyphSpinBox


@dataclass(frozen=True)
class AppSettings:
    theme: ThemeName
    size: int
    family: str
    language: str
    editor_path: str
    remote_large_file_mb: int


def _select_body_font_family(combo: ArrowComboBox, family: str) -> None:
    target = normalize_terminal_font_family(family)
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
    current_editor_path: str,
    current_remote_large_file_mb: int,
    *,
    on_save: Callable[[AppSettings], None] | None = None,
    min_width: int = 400,
) -> AppSettings | None:
    dialog = create_dialog(parent, tr('settings.title'), min_width=min_width)
    initial = AppSettings(
        theme=normalize_theme_name(current_theme),
        size=current_size,
        family=normalize_terminal_font_family(current_family),
        language=current_language or get_language(),
        editor_path=current_editor_path,
        remote_large_file_mb=current_remote_large_file_mb,
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
    preferred = get_app_config().appearance.terminal_font_families
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
    spin.setRange(terminal_font_size_min(), terminal_font_size_max())
    spin.setValue(initial.size)
    spin.setMinimumWidth(120)
    size_label = add_form_field(grid, 3, tr('settings.editor_font_size'), spin)

    editor_field = QWidget()
    editor_layout = QHBoxLayout(editor_field)
    editor_layout.setContentsMargins(0, 0, 0, 0)
    editor_layout.setSpacing(4)
    editor_edit = QLineEdit(initial.editor_path)
    editor_edit.setObjectName('DefaultEditorPathEdit')
    editor_browse = QPushButton(tr('settings.browse'))
    editor_browse.setObjectName('DefaultEditorBrowseButton')
    editor_layout.addWidget(editor_edit, 1)
    editor_layout.addWidget(editor_browse)
    editor_label = add_form_field(grid, 4, tr('settings.default_editor'), editor_field)

    remote_size_spin = GlyphSpinBox()
    remote_size_spin.setObjectName('RemoteEditLargeFileSpin')
    remote_size_spin.setRange(1, 10240)
    remote_size_spin.setValue(initial.remote_large_file_mb)
    remote_size_spin.setSuffix(' MiB')
    remote_size_label = add_form_field(
        grid,
        5,
        tr('settings.remote_edit_large_file'),
        remote_size_spin,
    )

    def browse_editor() -> None:
        selected, _ = get_open_file_name(
            dialog,
            tr('settings.select_editor'),
            editor_edit.text().strip(),
            tr('settings.executable_filter'),
        )
        if selected:
            editor_edit.setText(selected)

    editor_browse.clicked.connect(browse_editor)

    def current_settings() -> AppSettings:
        return AppSettings(
            theme=normalize_theme_name(theme_combo.currentData()),
            size=spin.value(),
            family=normalize_terminal_font_family(family_combo.currentText()),
            language=language_combo.currentData(),
            editor_path=editor_edit.text().strip(),
            remote_large_file_mb=remote_size_spin.value(),
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
    editor_edit.textChanged.connect(lambda _text: update_apply_enabled())
    remote_size_spin.valueChanged.connect(lambda _v: update_apply_enabled())

    def retranslate_settings_dialog() -> None:
        dialog.setWindowTitle(tr('settings.title'))
        theme_label.setText(tr('settings.theme'))
        language_label.setText(tr('settings.language'))
        family_label.setText(tr('settings.editor_font_family'))
        size_label.setText(tr('settings.editor_font_size'))
        editor_label.setText(tr('settings.default_editor'))
        editor_browse.setText(tr('settings.browse'))
        remote_size_label.setText(tr('settings.remote_edit_large_file'))
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
