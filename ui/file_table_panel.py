#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from ui.dialog_i18n import ask_yes_no
from ui.dialogs import prompt_text


def _format_size(size: int) -> str:
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size / (1024 * 1024):.1f} MB'


def _format_mtime(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except (OSError, ValueError):
        return ''


class _BaseFileTable(QTableWidget):
    path_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.SingleSelection)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self._current_path = ''
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)

    def current_path(self) -> str:
        return self._current_path

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        item = self.item(row, 0)
        if item is None:
            return
        entry_type = item.data(Qt.UserRole)
        name = item.text()
        if entry_type == 'dir':
            self._enter_directory(name)

    def _enter_directory(self, name: str) -> None:
        raise NotImplementedError


class LocalFileTable(_BaseFileTable):
    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels([
            tr('file.name'), tr('file.size'), tr('file.modified'),
        ])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._current_path = os.path.expanduser('~')

    def refresh(self) -> None:
        path = self._current_path
        self.setRowCount(0)
        parent = os.path.dirname(path.rstrip(os.sep))
        if parent and parent != path:
            self._append_row('..', 'dir', 0, 0.0)

        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return

        for name in entries:
            full = os.path.join(path, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            entry_type = 'dir' if os.path.isdir(full) else 'file'
            self._append_row(name, entry_type, stat.st_size, stat.st_mtime)

    def _append_row(self, name: str, entry_type: str, size: int, mtime: float) -> None:
        row = self.rowCount()
        self.insertRow(row)
        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.UserRole, entry_type)
        self.setItem(row, 0, name_item)
        self.setItem(row, 1, QTableWidgetItem('' if entry_type == 'dir' else _format_size(size)))
        self.setItem(row, 2, QTableWidgetItem(_format_mtime(mtime)))

    def _enter_directory(self, name: str) -> None:
        if name == '..':
            parent = os.path.dirname(self._current_path.rstrip(os.sep))
            self._current_path = parent or self._current_path
        else:
            self._current_path = os.path.join(self._current_path, name)
        self.path_changed.emit(self._current_path)
        self.refresh()

    def set_path(self, path: str) -> None:
        self._current_path = path
        self.path_changed.emit(path)
        self.refresh()


class RemoteFileTable(_BaseFileTable):
    upload_requested = pyqtSignal(list)
    download_requested = pyqtSignal(list)
    delete_requested = pyqtSignal(list)
    rename_requested = pyqtSignal(str, str)
    mkdir_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels([
            tr('file.name'), tr('file.size'), tr('file.modified'), tr('file.perm'),
        ])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._current_path = '/'
        self._list_callback: Optional[Callable[[str], Any]] = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _selected_entries(self) -> list[tuple[str, str]]:
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        result: list[tuple[str, str]] = []
        for row in rows:
            item = self.item(row, 0)
            if item is None or item.text() == '..':
                continue
            entry_type = item.data(Qt.UserRole) or 'file'
            result.append((item.text(), entry_type))
        return result

    def _remote_full_path(self, name: str) -> str:
        base = self._current_path.rstrip('/')
        return f'{base}/{name}' if base else f'/{name}'

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction(tr('file.refresh'), self.refresh_requested.emit)
        menu.addAction(tr('file.mkdir'), self._mkdir)
        selected = self._selected_entries()
        if selected:
            menu.addSeparator()
            menu.addAction(tr('file.download'), lambda: self._download_selected(selected))
            if len(selected) == 1:
                menu.addAction(tr('file.rename'), self._rename)
            menu.addAction(tr('file.delete'), self._delete_selected)
        menu.exec_(self.viewport().mapToGlobal(pos))

    def _mkdir(self) -> None:
        name = prompt_text(self, tr('file.mkdir'), tr('file.prompt_name'))
        if name:
            self.mkdir_requested.emit(name)

    def _rename(self) -> None:
        selected = self._selected_entries()
        if len(selected) != 1:
            return
        old_name, _ = selected[0]
        new_name = prompt_text(self, tr('file.rename'), tr('file.prompt_name'), initial=old_name)
        if new_name and new_name != old_name:
            self.rename_requested.emit(old_name, new_name)

    def _download_selected(self, selected: Optional[list[tuple[str, str]]] = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self.download_requested.emit([self._remote_full_path(name) for name, _ in selected])

    def _delete_selected(self) -> None:
        selected = self._selected_entries()
        if not selected:
            return
        if ask_yes_no(self, tr('file.delete'), tr('file.confirm_delete')):
            self.delete_requested.emit([self._remote_full_path(name) for name, _ in selected])

    def set_list_callback(self, callback: Callable[[str], Any]) -> None:
        self._list_callback = callback

    def refresh(self) -> None:
        if self._list_callback is None:
            self.setRowCount(0)
            return
        entries = self._list_callback(self._current_path) or []
        self.setRowCount(0)
        if self._current_path not in ('/', ''):
            self._append_row('..', 'dir', 0, '', '')

        for entry in entries:
            name = entry.get('name', '')
            entry_type = 'dir' if entry.get('is_dir') else 'file'
            size = int(entry.get('size', 0) or 0)
            mtime = float(entry.get('mtime', 0) or 0)
            perm = str(entry.get('perm', '') or '')
            self._append_row(name, entry_type, size, _format_mtime(mtime), perm)

    def _append_row(self, name: str, entry_type: str, size: int, mtime: str, perm: str) -> None:
        row = self.rowCount()
        self.insertRow(row)
        name_item = QTableWidgetItem(name)
        name_item.setData(Qt.UserRole, entry_type)
        self.setItem(row, 0, name_item)
        self.setItem(row, 1, QTableWidgetItem('' if entry_type == 'dir' else _format_size(size)))
        self.setItem(row, 2, QTableWidgetItem(mtime))
        self.setItem(row, 3, QTableWidgetItem(perm))

    def _enter_directory(self, name: str) -> None:
        if name == '..':
            parent = os.path.dirname(self._current_path.rstrip('/'))
            self._current_path = parent or '/'
        else:
            base = self._current_path.rstrip('/')
            self._current_path = f'{base}/{name}' if base else f'/{name}'
        self.path_changed.emit(self._current_path)
        self.refresh_requested.emit()

    def set_path(self, path: str) -> None:
        self._current_path = path or '/'
        self.path_changed.emit(self._current_path)
        self.refresh_requested.emit()

    def clear_remote(self) -> None:
        self._list_callback = None
        self.setRowCount(0)


class FilePanelWidget(QWidget):
    """Horizontal splitter with local and remote file tables."""

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._sftp_handler = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)

        local_panel = QWidget()
        local_layout = QVBoxLayout(local_panel)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_header = QHBoxLayout()
        local_header.addWidget(QLabel(tr('file.local')))
        self.local_path_edit = QLineEdit()
        self.local_path_edit.setPlaceholderText(tr('file.path_placeholder'))
        local_header.addWidget(self.local_path_edit, 1)
        self.local_refresh_btn = QPushButton(tr('file.refresh'))
        local_header.addWidget(self.local_refresh_btn)
        local_layout.addLayout(local_header)

        self.local_table = LocalFileTable()
        local_layout.addWidget(self.local_table, 1)

        remote_panel = QWidget()
        remote_layout = QVBoxLayout(remote_panel)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        remote_header = QHBoxLayout()
        remote_header.addWidget(QLabel(tr('file.remote')))
        self.remote_path_edit = QLineEdit()
        self.remote_path_edit.setPlaceholderText(tr('file.path_placeholder'))
        remote_header.addWidget(self.remote_path_edit, 1)
        self.remote_refresh_btn = QPushButton(tr('file.refresh'))
        remote_header.addWidget(self.remote_refresh_btn)
        remote_layout.addLayout(remote_header)

        self.remote_table = RemoteFileTable()
        self.remote_placeholder = QLabel(tr('file.not_connected'))
        self.remote_placeholder.setAlignment(Qt.AlignCenter)
        remote_layout.addWidget(self.remote_placeholder)
        remote_layout.addWidget(self.remote_table, 1)
        self.remote_table.hide()

        splitter.addWidget(local_panel)
        splitter.addWidget(remote_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, 1)

        self.local_table.path_changed.connect(self.local_path_edit.setText)
        self.remote_table.path_changed.connect(self.remote_path_edit.setText)
        self.local_path_edit.returnPressed.connect(self._local_path_entered)
        self.remote_path_edit.returnPressed.connect(self._remote_path_entered)
        self.local_refresh_btn.clicked.connect(self.local_table.refresh)
        self.remote_refresh_btn.clicked.connect(self._remote_refresh)

        self.local_table.refresh()
        self.local_path_edit.setText(self.local_table.current_path())

    def _local_path_entered(self) -> None:
        path = self.local_path_edit.text().strip()
        if path:
            self.local_table.set_path(path)

    def _remote_path_entered(self) -> None:
        path = self.remote_path_edit.text().strip()
        if path:
            self.remote_table.set_path(path)

    def _remote_refresh(self) -> None:
        if self._sftp_handler is not None:
            self._sftp_handler.refresh_remote(self.remote_table.current_path())
        else:
            self.remote_table.refresh()

    def set_remote_list_callback(self, callback: Callable[[str], List[dict]]) -> None:
        self.remote_table.set_list_callback(callback)
        self.remote_placeholder.hide()
        self.remote_table.show()
        self._remote_refresh()

    def clear_remote(self) -> None:
        self.remote_table.clear_remote()
        self.remote_table.hide()
        self.remote_placeholder.show()
        self.remote_path_edit.clear()

    def retranslate_ui(self) -> None:
        self.local_refresh_btn.setText(tr('file.refresh'))
        self.remote_refresh_btn.setText(tr('file.refresh'))
        self.remote_placeholder.setText(tr('file.not_connected'))
        self.local_table.setHorizontalHeaderLabels([
            tr('file.name'), tr('file.size'), tr('file.modified'),
        ])
        self.remote_table.setHorizontalHeaderLabels([
            tr('file.name'), tr('file.size'), tr('file.modified'), tr('file.perm'),
        ])


    def set_sftp_handler(self, handler) -> None:
        if self._sftp_handler is not None:
            try:
                self.remote_table.upload_requested.disconnect()
                self.remote_table.download_requested.disconnect()
                self.remote_table.delete_requested.disconnect()
                self.remote_table.rename_requested.disconnect()
                self.remote_table.mkdir_requested.disconnect()
                self.remote_table.refresh_requested.disconnect()
            except TypeError:
                pass
        self._sftp_handler = handler
        if handler is None:
            return
        self.remote_table.upload_requested.connect(handler.upload_local_paths)
        self.remote_table.download_requested.connect(handler.download_remote_paths)
        self.remote_table.delete_requested.connect(handler.delete_remote_paths)
        self.remote_table.rename_requested.connect(handler.rename_remote)
        self.remote_table.mkdir_requested.connect(handler.mkdir_remote)
        self.remote_table.refresh_requested.connect(
            lambda: handler.refresh_remote(self.remote_table.current_path())
        )

    def refresh_remote_table(self) -> None:
        self.remote_table.refresh()
