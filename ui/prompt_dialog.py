#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QVBoxLayout, QWidget

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
) -> Optional[str]:
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
