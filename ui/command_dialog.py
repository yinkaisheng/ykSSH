#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create and edit quick commands."""
from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from log_util import logger
from models.command_item import CommandItem
from storage.app_config import get_app_config, save_command_edit_dialog_size
from ui.dialog_common import add_form_field, create_form_grid
from ui.dialog_i18n import message_warning, translate_button_box


class CommandDialog(QDialog):
    """Create or edit a quick command."""

    def __init__(
        self,
        parent: QWidget,
        *,
        command: Optional[CommandItem] = None,
        title: str,
    ) -> None:
        super().__init__(parent)
        self._command = command
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        if command is not None:
            cfg = get_app_config().side_panel
            self.resize(cfg.command_edit_dialog_width, cfg.command_edit_dialog_height)

        layout = QVBoxLayout(self)
        grid = create_form_grid()

        self.name_edit = QLineEdit(command.name if command is not None else '')
        self.command_edit = QTextEdit(command.command if command is not None else '')
        self.command_edit.setAcceptRichText(False)
        self.command_edit.setMinimumHeight(90)
        self.description_edit = QTextEdit(command.description if command is not None else '')
        self.description_edit.setAcceptRichText(False)
        self.description_edit.setMinimumHeight(70)

        add_form_field(grid, 0, tr('commands.name'), self.name_edit)
        add_form_field(grid, 1, tr('commands.command'), self.command_edit)
        add_form_field(grid, 2, tr('commands.description'), self.description_edit)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        translate_button_box(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.setFocus()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._command is not None:
            try:
                save_command_edit_dialog_size(width=self.width(), height=self.height())
            except OSError as exc:
                logger.warning(f'Failed to save command edit dialog size: {exc}')
        super().closeEvent(event)

    def accept(self) -> None:  # type: ignore[override]
        if not self.name_edit.text().strip():
            message_warning(self, tr('commands.validation_title'), tr('commands.validation_name_required'))
            self.name_edit.setFocus()
            return
        if not self.command_edit.toPlainText().strip():
            message_warning(self, tr('commands.validation_title'), tr('commands.validation_command_required'))
            self.command_edit.setFocus()
            return
        super().accept()

    def get_command(self, existing: Optional[CommandItem] = None) -> Optional[CommandItem]:
        name = self.name_edit.text().strip()
        command = self.command_edit.toPlainText().strip()
        if not name or not command:
            return None
        item = existing or CommandItem()
        item.name = name
        item.command = command
        item.description = self.description_edit.toPlainText().strip()
        item.children = []
        return item
