#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Non-modal dialogs for managing local/remote file panel favorites."""
from __future__ import annotations

import os
from typing import Callable, List, Optional, Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import register_retranslator, tr, unregister_retranslator
from models.favorite_path import FavoritePath
from storage.app_config import get_app_config, save_favorites_dialog_size
from ui.dialog_i18n import translate_button_box


def _normalize_local_path(path: str) -> str:
    text = (path or '').strip()
    if not text:
        return ''
    return os.path.normpath(text)


class _FavoriteListEditor(QWidget):
    """Editable path/note table with add/remove controls."""

    def __init__(
        self,
        parent: QWidget = None,
        *,
        title: str = '',
        allow_browse: bool = False,
        browse_start_provider: Optional[Callable[[], str]] = None,
    ) -> None:
        super().__init__(parent)
        self._allow_browse = allow_browse
        self._browse_start_provider = browse_start_provider
        self._title_text = title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._title_label = QLabel(title)
        layout.addWidget(self._title_label)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels([
            tr('file.favorites.col_path'),
            tr('file.favorites.col_note'),
        ])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self._table.horizontalHeader().resizeSection(1, 140)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(
            QTableWidget.DoubleClicked
            | QTableWidget.SelectedClicked
            | QTableWidget.EditKeyPressed
            | QTableWidget.AnyKeyPressed,
        )
        layout.addWidget(self._table, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self._add_btn = QPushButton(tr('file.favorites.add'))
        self._browse_btn = QPushButton(tr('file.favorites.browse'))
        self._remove_btn = QPushButton(tr('file.favorites.remove'))
        self._add_btn.clicked.connect(self._add_empty_row)
        self._browse_btn.clicked.connect(self._browse_and_add)
        self._remove_btn.clicked.connect(self._remove_selected)
        buttons.addWidget(self._add_btn)
        if allow_browse:
            buttons.addWidget(self._browse_btn)
        else:
            self._browse_btn.hide()
        buttons.addWidget(self._remove_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def set_title(self, title: str) -> None:
        self._title_text = title
        self._title_label.setText(title)

    def set_entries(self, entries: Sequence[FavoritePath]) -> None:
        self._table.setRowCount(0)
        for entry in entries:
            self._append_row(entry.path, entry.note, start_edit=False)

    def entries(self) -> List[FavoritePath]:
        result: List[FavoritePath] = []
        for row in range(self._table.rowCount()):
            path_item = self._table.item(row, 0)
            note_item = self._table.item(row, 1)
            path = path_item.text().strip() if path_item else ''
            note = note_item.text().strip() if note_item else ''
            if path:
                result.append(FavoritePath(path=path, note=note))
        return result

    def retranslate_ui(self) -> None:
        self._title_label.setText(self._title_text)
        self._table.setHorizontalHeaderLabels([
            tr('file.favorites.col_path'),
            tr('file.favorites.col_note'),
        ])
        self._add_btn.setText(tr('file.favorites.add'))
        self._browse_btn.setText(tr('file.favorites.browse'))
        self._remove_btn.setText(tr('file.favorites.remove'))

    def _append_row(self, path: str = '', note: str = '', *, start_edit: bool = True) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(path))
        self._table.setItem(row, 1, QTableWidgetItem(note))
        self._table.setCurrentCell(row, 0)
        if start_edit:
            self._table.editItem(self._table.item(row, 0))

    def _add_empty_row(self) -> None:
        self._append_row()

    def _browse_and_add(self) -> None:
        start = ''
        if self._browse_start_provider is not None:
            start = (self._browse_start_provider() or '').strip()
        path = QFileDialog.getExistingDirectory(
            self,
            tr('file.favorites.browse_title'),
            start,
        )
        if path:
            self._append_row(_normalize_local_path(path))

    def _remove_selected(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)


class LocalFavoritesDialog(QWidget):
    """Non-modal dialog: global local favorites | session local favorites."""

    def __init__(
        self,
        parent: QWidget = None,
        *,
        global_entries: Sequence[FavoritePath],
        session_entries: Sequence[FavoritePath],
        browse_start_provider: Optional[Callable[[], str]] = None,
        on_save: Optional[Callable[[List[FavoritePath], List[FavoritePath]], None]] = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._on_save = on_save
        self.setWindowTitle(tr('file.favorites.local_manage_title'))
        cfg = get_app_config().file_panel
        self.setMinimumSize(480, 280)
        self.resize(cfg.local_favorites_dialog_width, cfg.local_favorites_dialog_height)

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        self._global_editor = _FavoriteListEditor(
            self,
            title=tr('file.favorites.global_local'),
            allow_browse=True,
            browse_start_provider=browse_start_provider,
        )
        self._session_editor = _FavoriteListEditor(
            self,
            title=tr('file.favorites.session_local'),
            allow_browse=True,
            browse_start_provider=browse_start_provider,
        )
        self._global_editor.set_entries(global_entries)
        self._session_editor.set_entries(session_entries)
        splitter.addWidget(self._global_editor)
        splitter.addWidget(self._session_editor)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        translate_button_box(buttons)
        save_btn = buttons.button(QDialogButtonBox.Save)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if save_btn is not None:
            save_btn.clicked.connect(self._save)
        if close_btn is not None:
            close_btn.clicked.connect(self.close)
        layout.addWidget(buttons)
        self._buttons = buttons

        register_retranslator(self.retranslate_ui)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        save_favorites_dialog_size(local=True, width=self.width(), height=self.height())
        unregister_retranslator(self.retranslate_ui)
        super().closeEvent(event)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr('file.favorites.local_manage_title'))
        self._global_editor.set_title(tr('file.favorites.global_local'))
        self._session_editor.set_title(tr('file.favorites.session_local'))
        self._global_editor.retranslate_ui()
        self._session_editor.retranslate_ui()
        translate_button_box(self._buttons)

    def _save(self) -> None:
        if self._on_save is not None:
            self._on_save(self._global_editor.entries(), self._session_editor.entries())


class RemoteFavoritesDialog(QWidget):
    """Non-modal dialog for session remote favorites."""

    def __init__(
        self,
        parent: QWidget = None,
        *,
        session_entries: Sequence[FavoritePath],
        on_save: Optional[Callable[[List[FavoritePath]], None]] = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._on_save = on_save
        self.setWindowTitle(tr('file.favorites.remote_manage_title'))
        cfg = get_app_config().file_panel
        self.setMinimumSize(360, 240)
        self.resize(cfg.remote_favorites_dialog_width, cfg.remote_favorites_dialog_height)

        layout = QVBoxLayout(self)
        self._editor = _FavoriteListEditor(
            self,
            title=tr('file.favorites.session_remote'),
            allow_browse=False,
        )
        self._editor.set_entries(session_entries)
        layout.addWidget(self._editor, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        translate_button_box(buttons)
        save_btn = buttons.button(QDialogButtonBox.Save)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if save_btn is not None:
            save_btn.clicked.connect(self._save)
        if close_btn is not None:
            close_btn.clicked.connect(self.close)
        layout.addWidget(buttons)
        self._buttons = buttons

        register_retranslator(self.retranslate_ui)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        save_favorites_dialog_size(local=False, width=self.width(), height=self.height())
        unregister_retranslator(self.retranslate_ui)
        super().closeEvent(event)

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr('file.favorites.remote_manage_title'))
        self._editor.set_title(tr('file.favorites.session_remote'))
        self._editor.retranslate_ui()
        translate_button_box(self._buttons)

    def _save(self) -> None:
        if self._on_save is not None:
            self._on_save(self._editor.entries())


def show_local_favorites_dialog(
    parent: QWidget,
    *,
    global_entries: Sequence[FavoritePath],
    session_entries: Sequence[FavoritePath],
    current_path_provider: Optional[Callable[[], str]] = None,
    on_save: Optional[Callable[[List[FavoritePath], List[FavoritePath]], None]] = None,
) -> LocalFavoritesDialog:
    dialog = LocalFavoritesDialog(
        parent,
        global_entries=global_entries,
        session_entries=session_entries,
        browse_start_provider=current_path_provider,
        on_save=on_save,
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def show_remote_favorites_dialog(
    parent: QWidget,
    *,
    session_entries: Sequence[FavoritePath],
    current_path_provider: Optional[Callable[[], str]] = None,
    on_save: Optional[Callable[[List[FavoritePath]], None]] = None,
) -> RemoteFavoritesDialog:
    del current_path_provider  # kept for call-site compatibility
    dialog = RemoteFavoritesDialog(
        parent,
        session_entries=session_entries,
        on_save=on_save,
    )
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
