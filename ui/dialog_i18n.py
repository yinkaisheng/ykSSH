#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translate Qt standard dialog buttons via strings.txt (no Qt .qm files)."""
from __future__ import annotations

from typing import Optional, Tuple

from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QWidget,
)

from i18n import get_language, register_retranslator, tr
from i18n.translator import DEFAULT_LOCALE

# (i18n key, label hint for display)
_DIALOG_BUTTON_BOX_SPECS = {
    QDialogButtonBox.Ok: ('dialog.ok', 'O'),
    QDialogButtonBox.Cancel: ('dialog.cancel', 'Esc'),
    QDialogButtonBox.Apply: ('dialog.apply', 'A'),
    QDialogButtonBox.Save: ('dialog.save', 'S'),
    QDialogButtonBox.Close: ('dialog.close', 'Esc'),
}

_MESSAGE_BOX_BUTTONS = (
    (QMessageBox.Yes, 'dialog.yes', 'Y'),
    (QMessageBox.No, 'dialog.no', 'N'),
    (QMessageBox.Cancel, 'dialog.cancel', 'Esc'),
    (QMessageBox.Ok, 'dialog.ok', 'O'),
    (QMessageBox.Apply, 'dialog.apply', 'A'),
    (QMessageBox.Save, 'dialog.save', 'S'),
    (QMessageBox.Close, 'dialog.close', 'Esc'),
)

_FILE_DIALOG_BUTTON_KEYS = {
    'Open': ('dialog.open', 'O'),
    '&Open': ('dialog.open', 'O'),
    'Save': ('dialog.save', 'S'),
    '&Save': ('dialog.save', 'S'),
    'Cancel': ('dialog.cancel', 'Esc'),
    '&Cancel': ('dialog.cancel', 'Esc'),
}

_INSTALLED = False


def _use_mnemonic_labels() -> bool:
    return get_language() == DEFAULT_LOCALE


def _insert_mnemonic(base: str, letter: str) -> str:
    for index, char in enumerate(base):
        if char.lower() == letter.lower():
            return f'{base[:index]}&{base[index:]}'
    return f'&{base}'


def _dialog_button_label(i18n_key: str, hint: Optional[str]) -> str:
    """English: &Yes style mnemonics; other locales: 是(Y) style."""
    base = tr(i18n_key)
    if not hint:
        return base
    if _use_mnemonic_labels():
        if hint == 'Esc':
            return f'{base} (Esc)'
        if len(hint) == 1:
            return _insert_mnemonic(base, hint)
        return f'{base} ({hint})'
    display = hint.upper() if len(hint) == 1 else hint
    return f'{base}({display})'


def _effective_shortcut(hint: Optional[str]) -> Optional[QKeySequence]:
    if not hint:
        return None
    if hint == 'Esc':
        return QKeySequence('Esc')
    if len(hint) == 1:
        if hint in ('Y', 'N'):
            return QKeySequence(hint)
        if _use_mnemonic_labels():
            return None
        return QKeySequence(f'Alt+{hint}')
    return None


def _configure_dialog_button(
    button: QPushButton,
    i18n_key: str,
    hint: Optional[str],
) -> None:
    button.setText(_dialog_button_label(i18n_key, hint))
    shortcut = _effective_shortcut(hint)
    if shortcut is not None:
        button.setShortcut(shortcut)


def _translate_message_box_buttons(box: QMessageBox) -> None:
    for role, i18n_key, hint in _MESSAGE_BOX_BUTTONS:
        btn = box.button(role)
        if btn is None:
            continue
        _configure_dialog_button(btn, i18n_key, hint)


def translate_button_box(button_box: QDialogButtonBox) -> None:
    for btn_flag, (i18n_key, hint) in _DIALOG_BUTTON_BOX_SPECS.items():
        btn = button_box.button(btn_flag)
        if btn is not None:
            _configure_dialog_button(btn, i18n_key, hint)


def _translate_file_dialog_buttons(dialog: QWidget) -> None:
    for button_box in dialog.findChildren(QDialogButtonBox):
        translate_button_box(button_box)
    for btn in dialog.findChildren(QPushButton):
        raw = btn.text()
        clean = raw.replace('&', '').split('(', 1)[0].strip()
        spec = _FILE_DIALOG_BUTTON_KEYS.get(clean) or _FILE_DIALOG_BUTTON_KEYS.get(raw.replace('&', ''))
        if spec is not None:
            i18n_key, hint = spec
            _configure_dialog_button(btn, i18n_key, hint)


def _retranslate_open_dialogs() -> None:
    app = QApplication.instance()
    if app is None:
        return
    for button_box in app.findChildren(QDialogButtonBox):
        translate_button_box(button_box)
    for dialog in app.findChildren(QFileDialog):
        _translate_file_dialog_buttons(dialog)


def _wrap_show_event(original):
    def show_event(self, event):
        if isinstance(self, QDialogButtonBox):
            translate_button_box(self)
        elif isinstance(self, QFileDialog):
            _translate_file_dialog_buttons(self)
        original(self, event)

    return show_event


def install_dialog_translations(app=None) -> None:
    del app  # kept for call-site compatibility
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    QDialogButtonBox.showEvent = _wrap_show_event(QDialogButtonBox.showEvent)
    QFileDialog.showEvent = _wrap_show_event(QFileDialog.showEvent)
    register_retranslator(_retranslate_open_dialogs)


def message_warning(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Ok, parent)
    _translate_message_box_buttons(box)
    box.exec_()


def message_info(parent: QWidget, title: str, text: str) -> None:
    box = QMessageBox(QMessageBox.Information, title, text, QMessageBox.Ok, parent)
    _translate_message_box_buttons(box)
    box.exec_()


def ask_yes_no(
    parent: QWidget,
    title: str,
    text: str,
    *,
    default: int = QMessageBox.No,
) -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.NoButton, parent)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    _translate_message_box_buttons(box)
    box.setDefaultButton(QMessageBox.Yes if default == QMessageBox.Yes else QMessageBox.No)
    return box.exec_() == QMessageBox.Yes


def ask_yes_no_cancel(
    parent: QWidget,
    title: str,
    text: str,
    *,
    default: int = QMessageBox.Cancel,
) -> int:
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.NoButton, parent)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
    _translate_message_box_buttons(box)
    box.setDefaultButton(default)
    return box.exec_()


def get_open_file_name(
    parent: QWidget,
    title: str,
    directory: str = '',
    file_filter: str = '',
) -> Tuple[str, str]:
    dialog = QFileDialog(parent, title, directory, file_filter)
    dialog.setFileMode(QFileDialog.ExistingFile)
    if dialog.exec_() == QDialog.Accepted:
        files = dialog.selectedFiles()
        if files:
            return files[0], dialog.selectedNameFilter()
    return '', file_filter


def get_save_file_name(
    parent: QWidget,
    title: str,
    default_name: str = '',
    file_filter: str = '',
) -> Tuple[str, str]:
    dialog = QFileDialog(parent, title, '', file_filter)
    dialog.setAcceptMode(QFileDialog.AcceptSave)
    if default_name:
        dialog.selectFile(default_name)
    if dialog.exec_() == QDialog.Accepted:
        files = dialog.selectedFiles()
        if files:
            return files[0], dialog.selectedNameFilter()
    return '', file_filter
