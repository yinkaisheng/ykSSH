#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations



from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QShortcut,
    QVBoxLayout,
    QWidget,
)

from ui.dialog_common import add_form_field, create_dialog, create_form_grid
from ui.dialog_i18n import translate_button_box


def prompt_text(
    parent: QWidget,
    title: str,
    label: str,
    initial: str = '',
    *,
    min_width: int = 400,
    allow_empty: bool = False,
) -> str | None:
    dialog = create_dialog(parent, title, min_width=min_width)

    layout = QVBoxLayout(dialog)
    grid = create_form_grid()
    edit = QLineEdit(initial)
    edit.setMinimumWidth(min_width - 48)
    add_form_field(grid, 0, label, edit)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
    translate_button_box(buttons)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addLayout(grid)
    layout.addWidget(buttons)

    edit.selectAll()
    edit.setFocus()

    if dialog.exec_() != QDialog.Accepted:
        return None
    text = edit.text().strip()
    if not text and not allow_empty:
        return None
    return text


def prompt_multiline_confirm(
    parent: QWidget,
    title: str,
    label: str,
    initial: str,
    *,
    min_width: int = 480,
    min_height: int = 240,
) -> str | None:
    """Show a resizable Yes/No dialog with an editable multiline body.

    Returns the edited text when Yes is chosen and the content is non-empty
    after strip; otherwise returns None. Leading/trailing whitespace in the
    returned text is preserved.
    """
    dialog = create_dialog(parent, title, min_width=min_width)
    dialog.setMinimumHeight(min_height)
    dialog.setSizeGripEnabled(True)

    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(label))

    edit = QPlainTextEdit()
    edit.setPlainText(initial)
    edit.setLineWrapMode(QPlainTextEdit.NoWrap)
    layout.addWidget(edit, stretch=1)

    buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No, parent=dialog)
    yes_btn = buttons.button(QDialogButtonBox.Yes)
    no_btn = buttons.button(QDialogButtonBox.No)
    if yes_btn is not None:
        yes_btn.setProperty('yksshDialogShortcutOverride', '')
        yes_btn.clicked.connect(dialog.accept)
    if no_btn is not None:
        no_btn.setProperty('yksshDialogShortcutOverride', '')
        no_btn.clicked.connect(dialog.reject)
    # Clear the buttons' own shortcuts/mnemonics and use dialog-level
    # shortcuts so the focused editor cannot consume Alt+Y / Alt+N.
    translate_button_box(buttons)
    yes_shortcut = QShortcut(QKeySequence('Alt+Y'), dialog)
    yes_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
    yes_shortcut.activated.connect(dialog.accept)
    no_shortcut = QShortcut(QKeySequence('Alt+N'), dialog)
    no_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
    no_shortcut.activated.connect(dialog.reject)
    layout.addWidget(buttons)

    metrics = QFontMetrics(edit.font())
    lines = initial.splitlines() or ['']
    line_count = max(len(lines), 1)
    max_line_chars = max((len(line) for line in lines), default=1)
    if hasattr(metrics, 'horizontalAdvance'):
        char_w = metrics.horizontalAdvance('M')
    else:
        char_w = metrics.width('M')
    content_w = char_w * min(max_line_chars + 4, 120) + 48
    content_h = metrics.lineSpacing() * min(line_count + 2, 40) + 120

    screen = QApplication.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        max_w = int(geo.width() * 0.8)
        max_h = int(geo.height() * 0.7)
    else:
        max_w, max_h = 900, 700

    width = max(min_width, min(content_w, max_w))
    height = max(min_height, min(content_h, max_h))
    dialog.resize(width, height)

    edit.setFocus()
    if dialog.exec_() != QDialog.Accepted:
        return None
    text = edit.toPlainText()
    if not text.strip():
        return None
    return text
