#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Callable, List, Optional

from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetrics, QShowEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QStyleOptionHeader,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from storage.app_config import get_app_config, save_file_panel_column_widths
from ui.dialog_i18n import ask_yes_no
from ui.prompt_dialog import prompt_text

SORT_RANK = Qt.UserRole + 1
SORT_NAME = Qt.UserRole + 2
SORT_SIZE = Qt.UserRole + 3
SORT_MTIME = Qt.UserRole + 4
SORT_PERM = Qt.UserRole + 5


class EqualSplitSplitter(QSplitter):
    """Horizontal/vertical splitter; double-click handle resets to 1:1."""

    def createHandle(self) -> QSplitterHandle:  # type: ignore[override]
        handle = super().createHandle()
        handle.installEventFilter(self)
        return handle

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if (
            isinstance(obj, QSplitterHandle)
            and event.type() == QEvent.MouseButtonDblClick
            and event.button() == Qt.LeftButton
        ):
            self.reset_equal_sizes()
            return True
        return super().eventFilter(obj, event)

    def reset_equal_sizes(self) -> None:
        total = sum(self.sizes())
        if total <= 0:
            return
        half = total // 2
        self.setSizes([half, total - half])


def _wrap_file_table(table: QTableWidget) -> QFrame:
    """Host frame draws the rounded border so corners are not clipped by the viewport."""
    host = QFrame()
    host.setObjectName('fileTableHost')
    host.setFrameShape(QFrame.NoFrame)
    layout = QVBoxLayout(host)
    # Keep the table inside the 1px border so corners stay filled.
    layout.setContentsMargins(1, 1, 1, 1)
    layout.setSpacing(0)
    table.setObjectName('fileTableInner')
    layout.addWidget(table)
    return host


def _format_size(size: int) -> str:
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size / (1024 * 1024):.1f} MB'


def _format_mtime(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    except (OSError, ValueError):
        return ''


def _entry_sort_rank(entry_type: str, *, is_parent: bool = False) -> int:
    if is_parent:
        return 0
    if entry_type == 'dir':
        return 1
    return 2


def _set_name_item_sort_keys(
    name_item: QTableWidgetItem,
    *,
    name: str,
    entry_type: str,
    size: int,
    mtime: float,
    perm: str = '',
    is_parent: bool = False,
) -> None:
    name_item.setData(Qt.UserRole, entry_type)
    name_item.setData(SORT_RANK, _entry_sort_rank(entry_type, is_parent=is_parent))
    name_item.setData(SORT_NAME, name.casefold())
    name_item.setData(SORT_SIZE, int(size))
    name_item.setData(SORT_MTIME, float(mtime))
    name_item.setData(SORT_PERM, perm.casefold())


class _FileSortItem(QTableWidgetItem):
    """Table item that sorts folders before files and supports per-column ordering."""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, QTableWidgetItem):
            return super().__lt__(other)

        table = self.tableWidget()
        if table is None:
            return super().__lt__(other)

        left_key = table.item(self.row(), 0)
        right_key = table.item(other.row(), 0)
        if left_key is None or right_key is None:
            return super().__lt__(other)

        left_rank = int(left_key.data(SORT_RANK) or 2)
        right_rank = int(right_key.data(SORT_RANK) or 2)
        if left_rank != right_rank:
            return left_rank < right_rank

        column = self.column()
        if column == 0:
            left = str(left_key.data(SORT_NAME) or '')
            right = str(right_key.data(SORT_NAME) or '')
            return left < right
        if column == 1:
            left = int(left_key.data(SORT_SIZE) or 0)
            right = int(right_key.data(SORT_SIZE) or 0)
            return left < right
        if column == 2:
            left = float(left_key.data(SORT_MTIME) or 0.0)
            right = float(right_key.data(SORT_MTIME) or 0.0)
            return left < right
        if column == 3:
            left = str(left_key.data(SORT_PERM) or '')
            right = str(right_key.data(SORT_PERM) or '')
            return left < right
        return super().__lt__(other)


def _make_sort_item(text: str) -> _FileSortItem:
    item = _FileSortItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class _FileTableHeaderView(QHeaderView):
    """Header view that keeps section labels in regular weight when the table is focused."""

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setObjectName('fileTableHeader')
        self._body_font = QFont(self.font())
        self._body_font.setBold(False)
        self._body_font.setWeight(QFont.Normal)
        self.setFont(self._body_font)
        self.setDefaultAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.setHighlightSections(False)

    def initStyleOption(self, option: QStyleOptionHeader) -> None:
        super().initStyleOption(option)
        option.font = self._body_font
        option.fontMetrics = QFontMetrics(self._body_font)


def _apply_column_widths(table: QTableWidget, column_widths: tuple[int, ...]) -> None:
    header = table.horizontalHeader()
    for index, width in enumerate(column_widths):
        if index >= table.columnCount():
            break
        header.setSectionResizeMode(index, QHeaderView.Interactive)
        table.setColumnWidth(index, width)


def _apply_file_table_layout(table: QTableWidget, column_widths: tuple[int, ...]) -> None:
    cfg = get_app_config().file_panel
    header = table.horizontalHeader()
    if not isinstance(header, _FileTableHeaderView):
        header = _FileTableHeaderView(table)
        table.setHorizontalHeader(header)
    header.setObjectName('fileTableHeader')
    header.setStretchLastSection(False)
    header.setSectionsMovable(False)
    header.setFixedHeight(cfg.header_height_px)

    vheader = table.verticalHeader()
    vheader.setDefaultSectionSize(cfg.row_height_px)
    vheader.setMinimumSectionSize(cfg.row_height_px)

    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    _apply_column_widths(table, column_widths)


class _BaseFileTable(QTableWidget):
    path_changed = pyqtSignal(str)

    DEFAULT_SORT_COLUMN = 0
    DEFAULT_SORT_ORDER = Qt.AscendingOrder

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setHorizontalHeader(_FileTableHeaderView(self))
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.SingleSelection)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setSortingEnabled(True)
        header = self.horizontalHeader()
        header.setSortIndicatorShown(True)
        header.setSectionsClickable(True)
        self._apply_default_sort()
        self._current_path = ''
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)

    def _apply_default_sort(self) -> None:
        self.horizontalHeader().setSortIndicator(
            self.DEFAULT_SORT_COLUMN,
            self.DEFAULT_SORT_ORDER,
        )

    def reset_sort_to_default(self) -> None:
        self._apply_default_sort()

    def apply_sort(self, column: int, order: Qt.SortOrder) -> None:
        header = self.horizontalHeader()
        self.setSortingEnabled(True)
        header.setSortIndicator(column, order)
        if self.rowCount() > 0:
            self.sortItems(column, order)

    def _current_sort(self) -> tuple[int, Qt.SortOrder]:
        header = self.horizontalHeader()
        column = header.sortIndicatorSection()
        if column < 0:
            return self.DEFAULT_SORT_COLUMN, self.DEFAULT_SORT_ORDER
        return column, header.sortIndicatorOrder()

    def _begin_refresh(self) -> tuple[int, Qt.SortOrder]:
        sort_column, sort_order = self._current_sort()
        self.setSortingEnabled(False)
        self.setRowCount(0)
        return sort_column, sort_order

    def _end_refresh(self, sort_column: int, sort_order: Qt.SortOrder) -> None:
        header = self.horizontalHeader()
        self.setSortingEnabled(True)
        header.setSortIndicator(sort_column, sort_order)
        self.sortItems(sort_column, sort_order)

    def _append_entry_row(
        self,
        name: str,
        entry_type: str,
        size: int,
        mtime: float,
        perm: str = '',
        *,
        is_parent: bool = False,
    ) -> None:
        row = self.rowCount()
        self.insertRow(row)
        name_item = _make_sort_item(name)
        _set_name_item_sort_keys(
            name_item,
            name=name,
            entry_type=entry_type,
            size=size,
            mtime=mtime,
            perm=perm,
            is_parent=is_parent,
        )
        self.setItem(row, 0, name_item)
        self.setItem(row, 1, _make_sort_item('' if entry_type == 'dir' else _format_size(size)))
        self.setItem(row, 2, _make_sort_item(_format_mtime(mtime) if mtime > 0 else ''))
        if self.columnCount() > 3:
            self.setItem(row, 3, _make_sort_item(perm))

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
        _apply_file_table_layout(self, get_app_config().file_panel.local_column_widths)
        self._current_path = os.path.expanduser('~')

    def refresh(self) -> None:
        path = self._current_path
        sort_column, sort_order = self._begin_refresh()
        parent = os.path.dirname(path.rstrip(os.sep))
        if parent and parent != path:
            self._append_entry_row('..', 'dir', 0, 0.0, is_parent=True)

        try:
            entries = os.listdir(path)
        except OSError:
            self._end_refresh(sort_column, sort_order)
            return

        dirs: list[tuple[str, int, float]] = []
        files: list[tuple[str, int, float]] = []
        for name in entries:
            full = os.path.join(path, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            if os.path.isdir(full):
                dirs.append((name, stat.st_size, stat.st_mtime))
            else:
                files.append((name, stat.st_size, stat.st_mtime))

        dirs.sort(key=lambda item: item[0].casefold())
        files.sort(key=lambda item: item[0].casefold())
        for name, size, mtime in dirs:
            self._append_entry_row(name, 'dir', size, mtime)
        for name, size, mtime in files:
            self._append_entry_row(name, 'file', size, mtime)
        self._end_refresh(sort_column, sort_order)

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
        _apply_file_table_layout(self, get_app_config().file_panel.remote_column_widths)
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
            self.setSortingEnabled(False)
            self.setRowCount(0)
            self.setSortingEnabled(True)
            return

        sort_column, sort_order = self._begin_refresh()
        entries = self._list_callback(self._current_path) or []
        if self._current_path not in ('/', ''):
            self._append_entry_row('..', 'dir', 0, 0.0, is_parent=True)

        dirs: list[dict] = []
        files: list[dict] = []
        for entry in entries:
            if entry.get('is_dir'):
                dirs.append(entry)
            else:
                files.append(entry)

        dirs.sort(key=lambda item: str(item.get('name', '') or '').casefold())
        files.sort(key=lambda item: str(item.get('name', '') or '').casefold())
        for entry in dirs + files:
            name = str(entry.get('name', '') or '')
            size = int(entry.get('size', 0) or 0)
            mtime = float(entry.get('mtime', 0) or 0)
            perm = str(entry.get('perm', '') or '')
            entry_type = 'dir' if entry.get('is_dir') else 'file'
            self._append_entry_row(name, entry_type, size, mtime, perm)
        self._end_refresh(sort_column, sort_order)

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


class LocalFilePanel(QWidget):
    """Local path bar + local file table."""

    path_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Optional[Callable[[bool, tuple[int, ...]], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self._label = QLabel(tr('file.local'))
        header.addWidget(self._label)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        header.addWidget(self.path_edit, 1)
        self.refresh_btn = QPushButton(tr('file.refresh'))
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.table = LocalFileTable()
        self._table_host = _wrap_file_table(self.table)
        layout.addWidget(self._table_host, 1)

        self.table.path_changed.connect(self.path_edit.setText)
        self.table.path_changed.connect(self.path_changed.emit)
        self.path_edit.returnPressed.connect(self._path_entered)
        self.refresh_btn.clicked.connect(self.table.refresh)
        self._setup_table_header_menu(is_local=True)
        self.table.refresh()
        self.path_edit.setText(self.table.current_path())

    def _setup_table_header_menu(self, *, is_local: bool) -> None:
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos, local=is_local: self._show_table_header_menu(local, pos),
        )

    def _show_table_header_menu(self, is_local: bool, pos) -> None:
        menu = QMenu(self)
        save_action = menu.addAction(tr('file.save_column_widths'))
        chosen = menu.exec_(self.table.horizontalHeader().mapToGlobal(pos))
        if chosen != save_action:
            return
        widths = tuple(self.table.columnWidth(index) for index in range(self.table.columnCount()))
        if self._on_save_column_widths is not None:
            self._on_save_column_widths(is_local, widths)

    def _path_entered(self) -> None:
        path = self.path_edit.text().strip()
        if path:
            self.table.set_path(path)

    def current_path(self) -> str:
        return self.table.current_path()

    def set_path(self, path: str) -> None:
        self.table.set_path(path)

    def refresh(self) -> None:
        self.table.refresh()

    def apply_column_widths(self, widths: tuple[int, ...]) -> None:
        _apply_column_widths(self.table, widths)

    def retranslate_ui(self) -> None:
        self._label.setText(tr('file.local'))
        self.refresh_btn.setText(tr('file.refresh'))
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        self.table.setHorizontalHeaderLabels([
            tr('file.name'), tr('file.size'), tr('file.modified'),
        ])


class RemoteFilePanel(QWidget):
    """Remote path bar + remote file table (or not-connected placeholder)."""

    path_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Optional[Callable[[bool, tuple[int, ...]], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._sftp_handler = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self._label = QLabel(tr('file.remote'))
        header.addWidget(self._label)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        header.addWidget(self.path_edit, 1)
        self.refresh_btn = QPushButton(tr('file.refresh'))
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.table = RemoteFileTable()
        self.placeholder = QLabel(tr('file.not_connected'))
        self.placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder_host = QFrame()
        self._placeholder_host.setObjectName('fileTableHost')
        self._placeholder_host.setFrameShape(QFrame.NoFrame)
        placeholder_layout = QVBoxLayout(self._placeholder_host)
        placeholder_layout.setContentsMargins(1, 1, 1, 1)
        placeholder_layout.addWidget(self.placeholder)
        self._table_host = _wrap_file_table(self.table)
        layout.addWidget(self._placeholder_host, 1)
        layout.addWidget(self._table_host, 1)
        self._table_host.hide()

        self.table.path_changed.connect(self.path_edit.setText)
        self.table.path_changed.connect(self.path_changed.emit)
        self.path_edit.returnPressed.connect(self._path_entered)
        self.refresh_btn.clicked.connect(self._remote_refresh)
        self._setup_table_header_menu(is_local=False)

    def _setup_table_header_menu(self, *, is_local: bool) -> None:
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos, local=is_local: self._show_table_header_menu(local, pos),
        )

    def _show_table_header_menu(self, is_local: bool, pos) -> None:
        menu = QMenu(self)
        save_action = menu.addAction(tr('file.save_column_widths'))
        chosen = menu.exec_(self.table.horizontalHeader().mapToGlobal(pos))
        if chosen != save_action:
            return
        widths = tuple(self.table.columnWidth(index) for index in range(self.table.columnCount()))
        if self._on_save_column_widths is not None:
            self._on_save_column_widths(is_local, widths)

    def _path_entered(self) -> None:
        path = self.path_edit.text().strip()
        if path:
            self.table.set_path(path)

    def _remote_refresh(self) -> None:
        if self._sftp_handler is not None:
            self._sftp_handler.refresh_remote(self.table.current_path())
        else:
            self.table.refresh()

    def current_path(self) -> str:
        return self.table.current_path()

    def set_path(self, path: str) -> None:
        self.table.set_path(path)

    def refresh(self) -> None:
        self.table.refresh()

    def apply_column_widths(self, widths: tuple[int, ...]) -> None:
        _apply_column_widths(self.table, widths)

    def set_list_callback(self, callback: Callable[[str], List[dict]]) -> None:
        self.table.set_list_callback(callback)
        self._placeholder_host.hide()
        self._table_host.show()
        self._remote_refresh()

    def clear_remote(self) -> None:
        self.table.clear_remote()
        self._table_host.hide()
        self._placeholder_host.show()
        self.path_edit.clear()

    def set_sftp_handler(self, handler) -> None:
        if self._sftp_handler is not None:
            try:
                self.table.upload_requested.disconnect()
                self.table.download_requested.disconnect()
                self.table.delete_requested.disconnect()
                self.table.rename_requested.disconnect()
                self.table.mkdir_requested.disconnect()
                self.table.refresh_requested.disconnect()
            except TypeError:
                pass
        self._sftp_handler = handler
        if handler is None:
            return
        self.table.upload_requested.connect(handler.upload_local_paths)
        self.table.download_requested.connect(handler.download_remote_paths)
        self.table.delete_requested.connect(handler.delete_remote_paths)
        self.table.rename_requested.connect(handler.rename_remote)
        self.table.mkdir_requested.connect(handler.mkdir_remote)
        self.table.refresh_requested.connect(
            lambda: handler.refresh_remote(self.table.current_path()),
        )

    def retranslate_ui(self) -> None:
        self._label.setText(tr('file.remote'))
        self.refresh_btn.setText(tr('file.refresh'))
        self.placeholder.setText(tr('file.not_connected'))
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        self.table.setHorizontalHeaderLabels([
            tr('file.name'), tr('file.size'), tr('file.modified'), tr('file.perm'),
        ])


class FilesPanel(QWidget):
    """One tab's file panel: local | splitter | remote."""

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Optional[Callable[[bool, tuple[int, ...]], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._splitter_layout_initialized = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.file_splitter = EqualSplitSplitter(Qt.Horizontal)
        self.file_splitter.setObjectName('filePanelSplitter')
        self.file_splitter.setChildrenCollapsible(False)
        self.file_splitter.setHandleWidth(6)

        self.local_file_panel = LocalFilePanel(
            on_save_column_widths=self._on_save_column_widths,
        )
        self.remote_file_panel = RemoteFilePanel(
            on_save_column_widths=self._on_save_column_widths,
        )

        self.file_splitter.addWidget(self.local_file_panel)
        self.file_splitter.addWidget(self.remote_file_panel)
        self.file_splitter.setStretchFactor(0, 1)
        self.file_splitter.setStretchFactor(1, 1)
        self.file_splitter.setSizes([1, 1])
        layout.addWidget(self.file_splitter, 1)

    def _init_splitter_layout_once(self) -> None:
        if self._splitter_layout_initialized:
            return
        if self.file_splitter.width() <= 0:
            QTimer.singleShot(50, self._init_splitter_layout_once)
            return
        self._splitter_layout_initialized = True
        self.file_splitter.reset_equal_sizes()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._splitter_layout_initialized:
            QTimer.singleShot(0, self._init_splitter_layout_once)

    def retranslate_ui(self) -> None:
        self.local_file_panel.retranslate_ui()
        self.remote_file_panel.retranslate_ui()


class FilePanelsContainer(QWidget):
    """Manages one FilesPanel per terminal tab; switches visible panel on tab change."""

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._panels: dict[str, FilesPanel] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)
        self._empty = QWidget()
        self._stack.addWidget(self._empty)
        self._stack.setCurrentWidget(self._empty)

    def create_panel(self, tab_id: str) -> FilesPanel:
        panel = self._panels.get(tab_id)
        if panel is not None:
            return panel
        panel = FilesPanel(on_save_column_widths=self._on_save_column_widths)
        self._panels[tab_id] = panel
        self._stack.addWidget(panel)
        return panel

    def get_panel(self, tab_id: str) -> Optional[FilesPanel]:
        return self._panels.get(tab_id)

    def show_panel(self, tab_id: str) -> Optional[FilesPanel]:
        panel = self._panels.get(tab_id)
        if panel is not None:
            self._stack.setCurrentWidget(panel)
        return panel

    def show_empty(self) -> None:
        self._stack.setCurrentWidget(self._empty)

    def remove_panel(self, tab_id: str) -> None:
        panel = self._panels.pop(tab_id, None)
        if panel is None:
            return
        self._stack.removeWidget(panel)
        panel.deleteLater()
        if self._stack.currentWidget() is panel:
            self.show_empty()

    def _on_save_column_widths(self, is_local: bool, widths: tuple[int, ...]) -> None:
        if is_local:
            save_file_panel_column_widths(local_column_widths=widths)
            for panel in self._panels.values():
                panel.local_file_panel.apply_column_widths(widths)
            return
        save_file_panel_column_widths(remote_column_widths=widths)
        for panel in self._panels.values():
            panel.remote_file_panel.apply_column_widths(widths)

    def retranslate_ui(self) -> None:
        for panel in self._panels.values():
            panel.retranslate_ui()
