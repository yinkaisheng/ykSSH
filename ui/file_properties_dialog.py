#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""File permission editor shared by local and remote file panels."""
from __future__ import annotations

import stat
from typing import Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from i18n import register_retranslator, tr, unregister_retranslator
from core.file_permissions import PermissionChange
from ui.dialog_i18n import translate_button_box
from ui.widgets import AccentCheckBox


_PERMISSION_BITS = (
    stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR,
    stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP,
    stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH,
)


def _format_property_size(size: int) -> str:
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    if size < 1024 * 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    return f'{size / (1024 * 1024 * 1024):.1f} GB'


class _ThemedCheckBox(AccentCheckBox):
    """Theme-aware checkbox which remains keyboard focusable in dialogs."""

    def __init__(self, text: str = '', parent: QWidget = None) -> None:
        super().__init__(text, parent)
        self.setFocusPolicy(Qt.StrongFocus)


class _PermissionCheckBox(_ThemedCheckBox):
    """Show a mixed state initially but cycle user clicks between on and off."""

    def nextCheckState(self) -> None:  # type: ignore[override]
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)


class FilePropertiesDialog(QDialog):
    """Edit rwx permissions with mixed states for multi-selection."""

    def __init__(
        self,
        modes: Sequence[int],
        *,
        has_directories: bool,
        windows_local: bool = False,
        file_count: int = 0,
        total_file_bytes: int = 0,
        parent: QWidget = None,
    ) -> None:
        super().__init__(parent)
        self._modes = [int(mode) for mode in modes]
        self._windows_local = windows_local
        self._file_count = max(0, int(file_count))
        self._total_file_bytes = max(0, int(total_file_bytes))
        self._checks: dict[int, QCheckBox] = {}
        self.setMinimumWidth(390)

        layout = QVBoxLayout(self)
        self._selection_label = QLabel()
        layout.addWidget(self._selection_label)

        self._scope_labels: list[QLabel] = []
        self._permission_labels: list[QLabel] = []
        self._readonly_check: QCheckBox | None = None
        if windows_local:
            write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
            readonly_values = [(mode & write_bits) == 0 for mode in self._modes]
            self._readonly_check = _PermissionCheckBox()
            self._readonly_check.setTristate(len(self._modes) > 1)
            if readonly_values and all(readonly_values):
                readonly_state = Qt.Checked
            elif any(readonly_values):
                readonly_state = Qt.PartiallyChecked
            else:
                readonly_state = Qt.Unchecked
            self._readonly_check.setCheckState(readonly_state)
            layout.addWidget(self._readonly_check)
        else:
            grid = QGridLayout()
            self._scope_labels = [QLabel(), QLabel(), QLabel()]
            self._permission_labels = [QLabel(), QLabel(), QLabel()]
            for column, label in enumerate(self._permission_labels, start=1):
                label.setAlignment(Qt.AlignCenter)
                grid.addWidget(label, 0, column)
            for row, label in enumerate(self._scope_labels, start=1):
                grid.addWidget(label, row, 0)
            for index, bit in enumerate(_PERMISSION_BITS):
                row = index // 3 + 1
                column = index % 3 + 1
                check = _PermissionCheckBox()
                check.setTristate(len(self._modes) > 1)
                check.setCheckState(self._initial_state(bit))
                check.setProperty('permissionBit', bit)
                grid.addWidget(check, row, column, alignment=Qt.AlignCenter)
                self._checks[bit] = check
            layout.addLayout(grid)

        self._recursive_check = _ThemedCheckBox()
        self._recursive_check.setVisible(has_directories)
        layout.addWidget(self._recursive_check)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        register_retranslator(self.retranslate_ui)
        self.retranslate_ui()

    def done(self, result: int) -> None:  # type: ignore[override]
        unregister_retranslator(self.retranslate_ui)
        super().done(result)

    def _initial_state(self, bit: int) -> Qt.CheckState:
        values = [(mode & bit) == bit for mode in self._modes]
        if values and all(values):
            return Qt.Checked
        if any(values):
            return Qt.PartiallyChecked
        return Qt.Unchecked

    def permission_change(self) -> PermissionChange:
        if self._readonly_check is not None:
            state = self._readonly_check.checkState()
            if state == Qt.PartiallyChecked:
                return PermissionChange(0, 0, self._recursive_check.isChecked())
            write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
            value = 0 if state == Qt.Checked else write_bits
            return PermissionChange(write_bits, value, self._recursive_check.isChecked())
        mask = 0
        value = 0
        for bit, check in self._checks.items():
            state = check.checkState()
            if state == Qt.PartiallyChecked:
                continue
            mask |= bit
            if state == Qt.Checked:
                value |= bit
        return PermissionChange(mask, value, self._recursive_check.isChecked())

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr('file.properties'))
        if self._file_count:
            self._selection_label.setText(tr(
                'file.properties.selected_files_size',
                count=len(self._modes),
                file_count=self._file_count,
                size=_format_property_size(self._total_file_bytes),
                bytes=f'{self._total_file_bytes:,}',
            ))
        else:
            self._selection_label.setText(tr('file.properties.selected', count=len(self._modes)))
        if self._readonly_check is not None:
            self._readonly_check.setText(tr('file.properties.readonly'))
        for label, key in zip(
            self._scope_labels,
            ('file.properties.owner', 'file.properties.group', 'file.properties.others'),
        ):
            label.setText(tr(key))
        for label, key in zip(
            self._permission_labels,
            ('file.properties.read', 'file.properties.write', 'file.properties.execute'),
        ):
            label.setText(tr(key))
        self._recursive_check.setText(tr('file.properties.recursive'))
        translate_button_box(self._buttons)
