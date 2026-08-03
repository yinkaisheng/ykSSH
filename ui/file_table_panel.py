#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
import stat
import sys
from datetime import datetime
from typing import Any, Callable, Iterable, List, Optional, Sequence

from PyQt5.QtCore import QEvent, Qt, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetrics, QMouseEvent, QShowEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QStyleOptionHeader,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.file_permissions import format_local_permission
from i18n import tr
from models.favorite_path import FavoritePath
from storage.app_config import get_app_config, save_file_panel_column_widths
from ui.dialog_i18n import ask_yes_no
from ui.file_panel_defaults import (
    DEFAULT_LOCAL_COLUMN_WIDTHS,
    DEFAULT_REMOTE_COLUMN_WIDTHS,
    FILE_TABLE_COLUMNS,
    column_widths_from_table,
)
from ui.prompt_dialog import prompt_text

_FILE_TABLE_COLUMN_LABEL_KEYS = {
    'Name': 'file.name',
    'Size': 'file.size',
    'Modified': 'file.modified',
    'Permissions': 'file.perm',
}

SORT_RANK = Qt.UserRole + 1
SORT_NAME = Qt.UserRole + 2
SORT_SIZE = Qt.UserRole + 3
SORT_MTIME = Qt.UserRole + 4
SORT_PERM = Qt.UserRole + 5
_PARENT_SORT_RANK = 0


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


_FILE_PANEL_TOOLBAR_TABLE_SPACING = 4


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
        return _PARENT_SORT_RANK
    if entry_type == 'dir':
        return 1
    return 2


def _item_sort_rank(item: QTableWidgetItem) -> int:
    value = item.data(SORT_RANK)
    if value is None:
        return 2
    return int(value)


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


def _apply_entry_name_font(name_item: QTableWidgetItem, entry_type: str) -> None:
    font = QFont(name_item.font())
    font.setBold(get_app_config().file_panel.folder_name_bold and entry_type == 'dir')
    name_item.setFont(font)


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

        left_rank = _item_sort_rank(left_key)
        right_rank = _item_sort_rank(right_key)
        left_is_parent = left_rank == _PARENT_SORT_RANK
        right_is_parent = right_rank == _PARENT_SORT_RANK
        if left_is_parent != right_is_parent:
            header = table.horizontalHeader()
            ascending = header.sortIndicatorSection() < 0 or header.sortIndicatorOrder() == Qt.AscendingOrder
            if left_is_parent:
                return ascending
            return not ascending
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


def _apply_file_panel_toolbar_layout(
    toolbar: QWidget,
    *,
    label: QLabel,
    path_edit: QLineEdit,
    nav_toolbar: Optional[QWidget] = None,
) -> None:
    cfg = get_app_config().file_panel
    toolbar_height = cfg.file_panel_toolbar_height
    toolbar.setFixedHeight(toolbar_height)
    font = QFont()
    font.setPixelSize(cfg.file_panel_toolbar_font_size)
    for widget in (label, path_edit):
        widget.setFont(font)
    path_edit.setFixedHeight(toolbar_height)
    if nav_toolbar is not None and hasattr(nav_toolbar, 'apply_layout'):
        nav_toolbar.apply_layout(toolbar_height, font)


def _file_table_header_labels(columns: tuple[str, ...] = FILE_TABLE_COLUMNS) -> list[str]:
    return [tr(_FILE_TABLE_COLUMN_LABEL_KEYS[key]) for key in columns]


def _apply_column_widths(
    table: QTableWidget,
    columns: tuple[str, ...],
    widths: dict[str, int],
    *,
    defaults: dict[str, int],
) -> None:
    header = table.horizontalHeader()
    for index, column in enumerate(columns):
        if index >= table.columnCount():
            break
        header.setSectionResizeMode(index, QHeaderView.Interactive)
        table.setColumnWidth(index, widths.get(column, defaults.get(column, 100)))


def _apply_file_table_layout(
    table: QTableWidget,
    columns: tuple[str, ...],
    widths: dict[str, int],
    *,
    defaults: dict[str, int],
) -> None:
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
    _apply_column_widths(table, columns, widths, defaults=defaults)


class _BaseFileTable(QTableWidget):
    path_changed = pyqtSignal(str)

    DEFAULT_SORT_COLUMN = 0
    DEFAULT_SORT_ORDER = Qt.AscendingOrder

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setHorizontalHeader(_FileTableHeaderView(self))
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.ExtendedSelection)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setSortingEnabled(True)
        header = self.horizontalHeader()
        header.setSortIndicatorShown(True)
        header.setSectionsClickable(True)
        self._current_path = ''
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)

    def _is_at_root(self) -> bool:
        raise NotImplementedError

    def _can_go_to_parent(self) -> bool:
        return not self._is_at_root()

    def _go_to_parent(self) -> None:
        if self._can_go_to_parent():
            self._enter_directory('..')

    def _is_blank_area_at(self, pos) -> bool:
        if not self.indexAt(pos).isValid():
            return True
        if self.rowCount() == 0:
            return True
        last_row = self.rowCount() - 1
        last_col = self.columnCount() - 1
        if last_col < 0:
            return True
        last_cell = self.model().index(last_row, last_col)
        last_rect = self.visualRect(last_cell)
        return pos.y() > last_rect.bottom() or pos.x() > last_rect.right()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._is_blank_area_at(event.pos()):
            if self._can_go_to_parent():
                self._go_to_parent()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _apply_default_sort(self) -> None:
        self.apply_sort(self.DEFAULT_SORT_COLUMN, self.DEFAULT_SORT_ORDER)

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
        if sort_column < 0:
            sort_column = self.DEFAULT_SORT_COLUMN
            sort_order = self.DEFAULT_SORT_ORDER
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
        _apply_entry_name_font(name_item, entry_type)
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
    upload_requested = pyqtSignal(list)

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setColumnCount(len(FILE_TABLE_COLUMNS))
        self.setHorizontalHeaderLabels(_file_table_header_labels())
        _apply_file_table_layout(
            self,
            FILE_TABLE_COLUMNS,
            get_app_config().file_panel.local_column_widths,
            defaults=DEFAULT_LOCAL_COLUMN_WIDTHS,
        )
        self._apply_default_sort()
        self._current_path = os.path.expanduser('~')
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _is_at_root(self) -> bool:
        return _is_local_root(self._current_path)

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

    def _local_full_path(self, name: str) -> str:
        return os.path.join(self._current_path, name)

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction(tr('file.refresh'), self.refresh)
        menu.addAction(tr('file.mkdir'), self._mkdir)
        selected = self._selected_entries()
        if selected:
            menu.addSeparator()
            menu.addAction(tr('file.copy_name'), lambda: self._copy_selected_names(selected))
            menu.addAction(tr('file.copy_path'), lambda: self._copy_selected_paths(selected))
            menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path)
            menu.addSeparator()
            menu.addAction(tr('file.upload'), lambda: self._upload_selected(selected))
            if len(selected) == 1:
                menu.addAction(tr('file.rename'), self._rename)
            menu.addAction(tr('file.delete'), self._delete_selected)
        else:
            menu.addSeparator()
            menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path)
        menu.exec_(self.viewport().mapToGlobal(pos))

    def _mkdir(self) -> None:
        name = prompt_text(self, tr('file.mkdir'), tr('file.prompt_name'))
        if not name:
            return
        try:
            os.mkdir(self._local_full_path(name))
        except OSError:
            return
        self.refresh()

    def _rename(self) -> None:
        selected = self._selected_entries()
        if len(selected) != 1:
            return
        old_name, _ = selected[0]
        new_name = prompt_text(self, tr('file.rename'), tr('file.prompt_name'), initial=old_name)
        if not new_name or new_name == old_name:
            return
        try:
            os.rename(self._local_full_path(old_name), self._local_full_path(new_name))
        except OSError:
            return
        self.refresh()

    def _copy_text(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None and text:
            clipboard.setText(text)

    def _copy_selected_names(self, selected: Optional[list[tuple[str, str]]] = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(name for name, _ in selected))

    def _copy_selected_paths(self, selected: Optional[list[tuple[str, str]]] = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(self._local_full_path(name) for name, _ in selected))

    def _copy_current_directory_path(self) -> None:
        self._copy_text(self._current_path)

    def _upload_selected(self, selected: Optional[list[tuple[str, str]]] = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self.upload_requested.emit([self._local_full_path(name) for name, _ in selected])

    def _delete_selected(self) -> None:
        selected = self._selected_entries()
        if not selected:
            return
        if not ask_yes_no(self, tr('file.delete'), tr('file.confirm_delete')):
            return
        for name, entry_type in selected:
            path = self._local_full_path(name)
            try:
                if entry_type == 'dir':
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError:
                continue
        self.refresh()

    def refresh(self) -> None:
        path = self._current_path
        sort_column, sort_order = self._begin_refresh()
        if not _is_local_root(path):
            self._append_entry_row('..', 'dir', 0, 0.0, is_parent=True)

        try:
            entries = os.listdir(path)
        except OSError:
            self._end_refresh(sort_column, sort_order)
            return

        dirs: list[tuple[str, int, float, str]] = []
        files: list[tuple[str, int, float, str]] = []
        for name in entries:
            if name in ('.', '..'):
                continue
            full = os.path.join(path, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            perm = format_local_permission(full)
            if stat.S_ISDIR(st.st_mode):
                dirs.append((name, st.st_size, st.st_mtime, perm))
            else:
                files.append((name, st.st_size, st.st_mtime, perm))

        dirs.sort(key=lambda item: item[0].casefold())
        files.sort(key=lambda item: item[0].casefold())
        for name, size, mtime, perm in dirs:
            self._append_entry_row(name, 'dir', size, mtime, perm)
        for name, size, mtime, perm in files:
            self._append_entry_row(name, 'file', size, mtime, perm)
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
        self.setColumnCount(len(FILE_TABLE_COLUMNS))
        self.setHorizontalHeaderLabels(_file_table_header_labels())
        _apply_file_table_layout(
            self,
            FILE_TABLE_COLUMNS,
            get_app_config().file_panel.remote_column_widths,
            defaults=DEFAULT_REMOTE_COLUMN_WIDTHS,
        )
        self._apply_default_sort()
        self._current_path = '/'
        self._list_callback: Optional[Callable[[str], Any]] = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _is_at_root(self) -> bool:
        return _is_remote_root(self._current_path)

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
            menu.addAction(tr('file.copy_name'), lambda: self._copy_selected_names(selected))
            menu.addAction(tr('file.copy_path'), lambda: self._copy_selected_paths(selected))
            menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path)
            menu.addSeparator()
            menu.addAction(tr('file.download'), lambda: self._download_selected(selected))
            if len(selected) == 1:
                menu.addAction(tr('file.rename'), self._rename)
            menu.addAction(tr('file.delete'), self._delete_selected)
        else:
            menu.addSeparator()
            menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path)
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

    def _copy_text(self, text: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None and text:
            clipboard.setText(text)

    def _copy_selected_names(self, selected: Optional[list[tuple[str, str]]] = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(name for name, _ in selected))

    def _copy_selected_paths(self, selected: Optional[list[tuple[str, str]]] = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(self._remote_full_path(name) for name, _ in selected))

    def _copy_current_directory_path(self) -> None:
        base = self._current_path.rstrip('/')
        self._copy_text(base if base else '/')

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
        if not _is_remote_root(self._current_path):
            self._append_entry_row('..', 'dir', 0, 0.0, is_parent=True)

        dirs: list[dict] = []
        files: list[dict] = []
        for entry in entries:
            name = str(entry.get('name', '') or '')
            if name in ('.', '..'):
                continue
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


def _list_windows_drives() -> List[str]:
    if sys.platform != 'win32':
        return []
    import ctypes

    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    drives: List[str] = []
    for index in range(26):
        if bitmask & (1 << index):
            drives.append(f'{chr(ord("A") + index)}:')
    return drives


def _windows_drive_root(path: str) -> str:
    drive, _ = os.path.splitdrive(path)
    if drive:
        return f'{drive}\\'
    return path


def _is_local_root(path: str) -> bool:
    if not path:
        return True
    normalized = os.path.abspath(os.path.normpath(path))
    if sys.platform == 'win32':
        _drive, tail = os.path.splitdrive(normalized)
        return tail in ('\\', '/')
    return normalized == '/'


def _is_remote_root(path: str) -> bool:
    text = (path or '/').strip()
    return text in ('', '/') or text.rstrip('/') == ''


class _FileNavToolbar(QWidget):
    """Square flat navigation buttons for local/remote file panel toolbars."""

    refresh_requested = pyqtSignal()
    favorites_requested = pyqtSignal()

    def __init__(self, parent: QWidget = None, *, local: bool = True) -> None:
        super().__init__(parent)
        self._local = local
        self.setObjectName('fileNavToolbar')
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._drive_buttons: list[QToolButton] = []
        self._drive_labels: list[str] = []

        if local and sys.platform == 'win32':
            for drive in _list_windows_drives():
                btn = self._make_text_button(drive)
                btn.clicked.connect(lambda _checked=False, target=drive: self._navigate_drive(target))
                self._drive_buttons.append(btn)
                self._drive_labels.append(drive)
                self._layout.addWidget(btn)

        self._root_btn = self._make_text_button('/')
        self._root_btn.clicked.connect(self._navigate_root)
        self._layout.addWidget(self._root_btn)

        self._home_btn = self._make_text_button('~')
        self._home_btn.clicked.connect(self._navigate_home)
        self._layout.addWidget(self._home_btn)

        self._favorites_btn = self._make_text_button('⭐') # star emoji ⭐★
        self._favorites_btn.clicked.connect(self.favorites_requested.emit)
        self._layout.addWidget(self._favorites_btn)

        # self._refresh_btn = self._make_icon_button('SP_BrowserReload')
        self._refresh_btn = self._make_text_button('🔄') # refresh emoji 🔃🔄
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        self._layout.addWidget(self._refresh_btn)

        self._path_provider: Optional[Callable[[], str]] = None
        self._home_path_provider: Optional[Callable[[], str]] = None
        self._navigate_handler: Optional[Callable[[str], None]] = None
        self.retranslate_ui()

    def set_path_provider(self, provider: Callable[[], str]) -> None:
        self._path_provider = provider

    def set_home_path_provider(self, provider: Callable[[], str]) -> None:
        self._home_path_provider = provider

    def set_navigate_handler(self, handler: Callable[[str], None]) -> None:
        self._navigate_handler = handler

    def favorites_button(self) -> QToolButton:
        return self._favorites_btn

    def _make_text_button(self, text: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName('filePanelNavButton')
        btn.setText(text)
        btn.setAutoRaise(True)
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        return btn

    def _make_icon_button(self, icon_name: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setObjectName('filePanelNavButton')
        btn.setAutoRaise(True)
        btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        icon = getattr(self.style().StandardPixmap, icon_name)
        btn.setIcon(self.style().standardIcon(icon))
        return btn

    def _all_buttons(self) -> Iterable[QToolButton]:
        yield from self._drive_buttons
        yield self._root_btn
        yield self._home_btn
        yield self._favorites_btn
        yield self._refresh_btn

    def apply_layout(self, toolbar_height: int, font: QFont) -> None:
        self.setFixedHeight(toolbar_height)
        icon_inner = max(12, toolbar_height - 8)
        icon_size = QSize(icon_inner, icon_inner)
        square = QSize(toolbar_height, toolbar_height)
        for btn in self._all_buttons():
            btn.setFont(font)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn.setMinimumSize(square)
            btn.setMaximumSize(square)
            btn.setFixedSize(square)
            if btn.toolButtonStyle() == Qt.ToolButtonIconOnly:
                btn.setIconSize(icon_size)

    def _current_path_value(self) -> str:
        if self._path_provider is not None:
            return self._path_provider()
        return ''

    def _navigate(self, path: str) -> None:
        if not path or self._navigate_handler is None:
            return
        self._navigate_handler(path)

    def _navigate_drive(self, drive: str) -> None:
        self._navigate(f'{drive}\\')

    def _navigate_root(self) -> None:
        if self._local and sys.platform == 'win32':
            current = self._current_path_value()
            self._navigate(_windows_drive_root(current))
        else:
            self._navigate('/')

    def _navigate_home(self) -> None:
        if self._home_path_provider is not None:
            home = self._home_path_provider().strip()
            if home:
                self._navigate(home)
                return
        if self._local:
            self._navigate(os.path.expanduser('~'))

    def retranslate_ui(self) -> None:
        current = self._current_path_value()
        if self._local and sys.platform == 'win32':
            drive_root = _windows_drive_root(current)
            self._root_btn.setToolTip(tr('file.local_nav.root_win', drive=drive_root))
        else:
            self._root_btn.setToolTip(tr('file.local_nav.root'))
        self._home_btn.setToolTip(tr('file.local_nav.home'))
        self._favorites_btn.setToolTip(tr('file.local_nav.favorites'))
        self._refresh_btn.setToolTip(tr('file.local_nav.refresh'))
        for btn, drive in zip(self._drive_buttons, self._drive_labels):
            btn.setToolTip(tr('file.local_nav.drive', drive=drive))


def _show_favorites_menu(
    parent: QWidget,
    anchor: QWidget,
    *,
    sections: Sequence[tuple[str, Sequence[FavoritePath]]],
    on_manage: Optional[Callable[[], None]],
    on_navigate: Optional[Callable[[str], None]],
) -> None:
    menu = QMenu(parent)
    menu_font = QFont()
    menu_font.setPixelSize(get_app_config().file_panel.file_panel_favorites_menu_font_size)
    menu.setFont(menu_font)
    manage_action = menu.addAction(tr('file.favorites.manage'))
    path_actions: list[tuple[QAction, str]] = []
    for title, entries in sections:
        if not entries:
            continue
        menu.addSeparator()
        if title:
            header = menu.addAction(title)
            header.setEnabled(False)
        for entry in entries:
            action = menu.addAction(entry.display_text())
            path_actions.append((action, entry.path))
    chosen = menu.exec_(anchor.mapToGlobal(anchor.rect().bottomLeft()))
    if chosen is None:
        return
    if chosen == manage_action:
        if on_manage is not None:
            on_manage()
        return
    for action, path in path_actions:
        if chosen == action and on_navigate is not None:
            on_navigate(path)
            return


class LocalFilePanel(QWidget):
    """Local path bar + local file table."""

    path_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Optional[Callable[[bool, dict[str, int]], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._favorites_provider: Optional[
            Callable[[], tuple[Sequence[FavoritePath], Sequence[FavoritePath]]]
        ] = None
        self._manage_favorites_handler: Optional[Callable[[], None]] = None
        self._sftp_handler = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_FILE_PANEL_TOOLBAR_TABLE_SPACING)
        self._toolbar = QWidget()
        self._toolbar.setObjectName('filePanelToolbar')
        header = QHBoxLayout(self._toolbar)
        header.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel(tr('file.local'))
        header.addWidget(self._label)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        header.addWidget(self.path_edit, 1)

        self.table = LocalFileTable()
        self._nav_toolbar = _FileNavToolbar(self, local=True)
        self._nav_toolbar.set_path_provider(self.table.current_path)
        self._nav_toolbar.set_navigate_handler(self.table.set_path)
        header.addWidget(self._nav_toolbar)
        _apply_file_panel_toolbar_layout(
            self._toolbar,
            label=self._label,
            path_edit=self.path_edit,
            nav_toolbar=self._nav_toolbar,
        )
        layout.addWidget(self._toolbar)

        layout.addWidget(self.table, 1)

        self.table.path_changed.connect(self.path_edit.setText)
        self.table.path_changed.connect(self.path_changed.emit)
        self.table.path_changed.connect(lambda _path: self._nav_toolbar.retranslate_ui())
        self.path_edit.returnPressed.connect(self._path_entered)
        self._nav_toolbar.refresh_requested.connect(self.table.refresh)
        self._nav_toolbar.favorites_requested.connect(self._show_favorites_menu)
        self._setup_table_header_menu(is_local=True)
        self.table.refresh()
        self.path_edit.setText(self.table.current_path())

    def set_favorites_provider(
        self,
        provider: Callable[[], tuple[Sequence[FavoritePath], Sequence[FavoritePath]]],
    ) -> None:
        """Provide (global_local_favorites, session_local_favorites)."""
        self._favorites_provider = provider

    def set_manage_favorites_handler(self, handler: Callable[[], None]) -> None:
        self._manage_favorites_handler = handler

    def set_sftp_handler(self, handler) -> None:
        if self._sftp_handler is not None:
            try:
                self.table.upload_requested.disconnect()
            except TypeError:
                pass
        self._sftp_handler = handler
        if handler is not None:
            self.table.upload_requested.connect(handler.upload_local_paths)

    def _show_favorites_menu(self) -> None:
        global_entries: Sequence[FavoritePath] = ()
        session_entries: Sequence[FavoritePath] = ()
        if self._favorites_provider is not None:
            global_entries, session_entries = self._favorites_provider()
        _show_favorites_menu(
            self,
            self._nav_toolbar.favorites_button(),
            sections=(
                (tr('file.favorites.global_local'), global_entries),
                (tr('file.favorites.session_local'), session_entries),
            ),
            on_manage=self._manage_favorites_handler,
            on_navigate=self.set_path,
        )

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
        widths = column_widths_from_table(
            [self.table.columnWidth(index) for index in range(self.table.columnCount())]
        )
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

    def apply_toolbar_layout(self) -> None:
        _apply_file_panel_toolbar_layout(
            self._toolbar,
            label=self._label,
            path_edit=self.path_edit,
            nav_toolbar=self._nav_toolbar,
        )

    def apply_column_widths(self, widths: dict[str, int]) -> None:
        _apply_column_widths(
            self.table,
            FILE_TABLE_COLUMNS,
            widths,
            defaults=DEFAULT_LOCAL_COLUMN_WIDTHS,
        )

    def retranslate_ui(self) -> None:
        self._label.setText(tr('file.local'))
        self._nav_toolbar.retranslate_ui()
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        self.table.setHorizontalHeaderLabels(_file_table_header_labels())


class RemoteFilePanel(QWidget):
    """Remote path bar + remote file table (or not-connected placeholder)."""

    path_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Optional[Callable[[bool, dict[str, int]], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._sftp_handler = None
        self._favorites_provider: Optional[Callable[[], Sequence[FavoritePath]]] = None
        self._manage_favorites_handler: Optional[Callable[[], None]] = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_FILE_PANEL_TOOLBAR_TABLE_SPACING)
        self._toolbar = QWidget()
        self._toolbar.setObjectName('filePanelToolbar')
        header = QHBoxLayout(self._toolbar)
        header.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel(tr('file.remote'))
        header.addWidget(self._label)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        header.addWidget(self.path_edit, 1)

        self.table = RemoteFileTable()
        self._nav_toolbar = _FileNavToolbar(self, local=False)
        self._nav_toolbar.set_path_provider(self.table.current_path)
        self._nav_toolbar.set_navigate_handler(self.table.set_path)
        self._nav_toolbar.set_home_path_provider(self._remote_home_path)
        header.addWidget(self._nav_toolbar)
        _apply_file_panel_toolbar_layout(
            self._toolbar,
            label=self._label,
            path_edit=self.path_edit,
            nav_toolbar=self._nav_toolbar,
        )
        layout.addWidget(self._toolbar)

        self.placeholder = QLabel(tr('file.not_connected'))
        self.placeholder.setObjectName('filePanelPlaceholder')
        self.placeholder.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.placeholder, 1)
        layout.addWidget(self.table, 1)
        self.table.hide()

        self.table.path_changed.connect(self.path_edit.setText)
        self.table.path_changed.connect(self.path_changed.emit)
        self.table.path_changed.connect(lambda _path: self._nav_toolbar.retranslate_ui())
        self.path_edit.returnPressed.connect(self._path_entered)
        self._nav_toolbar.refresh_requested.connect(self._remote_refresh)
        self._nav_toolbar.favorites_requested.connect(self._show_favorites_menu)
        self._setup_table_header_menu(is_local=False)

    def set_favorites_provider(self, provider: Callable[[], Sequence[FavoritePath]]) -> None:
        """Provide session remote favorites."""
        self._favorites_provider = provider

    def set_manage_favorites_handler(self, handler: Callable[[], None]) -> None:
        self._manage_favorites_handler = handler

    def _show_favorites_menu(self) -> None:
        entries: Sequence[FavoritePath] = ()
        if self._favorites_provider is not None:
            entries = self._favorites_provider()
        _show_favorites_menu(
            self,
            self._nav_toolbar.favorites_button(),
            sections=((tr('file.favorites.session_remote'), entries),),
            on_manage=self._manage_favorites_handler,
            on_navigate=self.set_path,
        )

    def _remote_home_path(self) -> str:
        if self._sftp_handler is not None:
            return self._sftp_handler.remote_home
        return '/'

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
        widths = column_widths_from_table(
            [self.table.columnWidth(index) for index in range(self.table.columnCount())]
        )
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

    def apply_toolbar_layout(self) -> None:
        _apply_file_panel_toolbar_layout(
            self._toolbar,
            label=self._label,
            path_edit=self.path_edit,
            nav_toolbar=self._nav_toolbar,
        )

    def apply_column_widths(self, widths: dict[str, int]) -> None:
        _apply_column_widths(
            self.table,
            FILE_TABLE_COLUMNS,
            widths,
            defaults=DEFAULT_REMOTE_COLUMN_WIDTHS,
        )

    def set_list_callback(self, callback: Callable[[str], List[dict]]) -> None:
        self.table.set_list_callback(callback)
        self.placeholder.hide()
        self.table.show()
        self._remote_refresh()

    def clear_remote(self) -> None:
        self.table.clear_remote()
        self.table.hide()
        self.placeholder.show()
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
        self._nav_toolbar.retranslate_ui()
        self.placeholder.setText(tr('file.not_connected'))
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        self.table.setHorizontalHeaderLabels(_file_table_header_labels())


class FilesPanel(QWidget):
    """One tab's file panel: local | splitter | remote."""

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Optional[Callable[[bool, dict[str, int]], None]] = None,
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
        self.file_splitter.setHandleWidth(4)

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

    def apply_toolbar_layout(self) -> None:
        self.local_file_panel.apply_toolbar_layout()
        self.remote_file_panel.apply_toolbar_layout()

    def apply_file_panel_layout(self) -> None:
        self.apply_toolbar_layout()
        self.local_file_panel.refresh()
        self.remote_file_panel.refresh()


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

    def _on_save_column_widths(self, is_local: bool, widths: dict[str, int]) -> None:
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

    def apply_file_panel_layout(self) -> None:
        for panel in self._panels.values():
            panel.apply_file_panel_layout()
