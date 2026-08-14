#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
from datetime import datetime
from typing import Any, Callable, Iterable, List, Optional, Sequence

from PyQt5.QtCore import QEvent, Qt, QTimer, QSize, QRect, QFile, QItemSelectionModel, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QShowEvent
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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

from core.file_permissions import PermissionChange, format_local_permission
from i18n import tr
from log_util import logger
from models.favorite_path import FavoritePath
from storage.app_config import get_app_config, save_file_panel_column_widths
from ui.theme import active_theme_palette
from ui.dialog_i18n import ask_yes_no, message_warning
from ui.menu_shortcuts import ShortcutMenu, add_menu_key, exec_menu
from storage.file_panel_defaults import (
    DEFAULT_LOCAL_COLUMN_WIDTHS,
    DEFAULT_REMOTE_COLUMN_WIDTHS,
    FILE_TABLE_COLUMNS,
    column_widths_from_table,
)
from ui.prompt_dialog import prompt_text
from ui.file_properties_dialog import FilePropertiesDialog

try:
    from pypinyin import Style, lazy_pinyin, pinyin
except ImportError:  # pragma: no cover - optional until requirements are installed
    Style = None
    lazy_pinyin = None
    pinyin = None

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
PERMISSION_MODE = Qt.UserRole + 6
_PARENT_SORT_RANK = 0

# Used only to choose the editable stem; the actual rename operation accepts
# any valid filesystem name.  Compound suffixes must be checked first.
_COMMON_RENAME_SUFFIXES = frozenset({
    '.tar', '.tar.gz', '.tar.bz2', '.tar.xz', '.tar.zst', '.tar.lz', '.tar.lzma',
    '.tgz', '.tbz', '.tbz2', '.txz', '.tlz',
    '.7z', '.bz2', '.gz', '.xz', '.zst', '.zip', '.rar',
    '.apk', '.appimage', '.deb', '.dmg', '.exe', '.msi', '.rpm',
    '.csv', '.doc', '.docx', '.md', '.pdf', '.ppt', '.pptx', '.rtf', '.txt', '.xls', '.xlsx',
    '.bmp', '.gif', '.ico', '.jpeg', '.jpg', '.png', '.svg', '.tif', '.tiff', '.webp',
    '.aac', '.flac', '.m4a', '.mp3', '.ogg', '.wav',
    '.avi', '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.webm',
    '.c', '.cc', '.cpp', '.css', '.go', '.h', '.hpp', '.html', '.java', '.js', '.json',
    '.jsx', '.kt', '.lua', '.py', '.rs', '.sh', '.sql', '.ts', '.tsx', '.xml', '.yaml', '.yml',
})


from ui.file_panel.helpers import (
    _FileTableHeaderView,
    _InlineRenameEdit,
    _apply_local_permission_change,
    _apply_entry_name_font,
    _apply_file_table_layout,
    _file_table_header_labels,
    _format_mtime,
    _format_size,
    _make_sort_item,
    _pinyin_initial_filter_targets,
    _set_name_item_sort_keys,
    _wildcard_filter_match,
)

class _BaseFileTable(QTableWidget):
    path_changed = pyqtSignal(str)
    status_counts_changed = pyqtSignal(int, int, int, int)
    filter_text_changed = pyqtSignal(str)
    filter_focus_requested = pyqtSignal()
    filter_cancelled = pyqtSignal()
    favorites_menu_requested = pyqtSignal()
    property_status_changed = pyqtSignal(bool, int, int, int)
    edit_requested = pyqtSignal(list, bool)

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
        self._filter_text = ''
        self._filter_edit_focused = False
        self._pending_select_name = ''
        self._inline_rename_edit: Optional[_InlineRenameEdit] = None
        self._inline_rename_item: Optional[QTableWidgetItem] = None
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.itemSelectionChanged.connect(self._emit_status_counts)

    def set_filter_edit_focused(self, focused: bool) -> None:
        self._filter_edit_focused = focused

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            # Ctrl + F
            self.filter_focus_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_D and event.modifiers() & Qt.ControlModifier:
            # Ctrl + D
            self.favorites_menu_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_F2 and not event.modifiers():
            self._rename()
            event.accept()
            return
        if event.key() == Qt.Key_F4 and not event.modifiers():
            if self._edit_selected(use_configured_editor=True):
                event.accept()
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers():
            if self._activate_current_directory():
                event.accept()
                return
        if event.key() == Qt.Key_Delete and event.modifiers() in (Qt.NoModifier, Qt.ShiftModifier):
            if self._handle_delete_key(permanent=bool(event.modifiers() & Qt.ShiftModifier)):
                event.accept()
                return
        if event.key() in (Qt.Key_Home, Qt.Key_End) and not event.modifiers():
            if self._jump_to_visible_edge(last=event.key() == Qt.Key_End):
                event.accept()
                return
        if event.key() == Qt.Key_Escape and self._filter_text:
            self.clear_filter()
            event.accept()
            return
        text = event.text()
        if text and text.isprintable() and not event.modifiers() & (
            Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
        ):
            if self._filter_text and not self._filter_edit_focused:
                self.set_filter_text(text)
            else:
                self.set_filter_text(self._filter_text + text)
            event.accept()
            return
        super().keyPressEvent(event)

    def _is_at_root(self) -> bool:
        raise NotImplementedError

    def _rename(self) -> None:
        raise NotImplementedError

    def _edit_selected(self, *, use_configured_editor: bool) -> bool:
        raise NotImplementedError

    def _jump_to_visible_edge(self, *, last: bool) -> bool:
        rows = range(self.rowCount() - 1, -1, -1) if last else range(self.rowCount())
        for row in rows:
            if self.isRowHidden(row):
                continue
            item = self.item(row, 0)
            if item is None:
                continue
            self.setCurrentCell(row, 0)
            self.selectRow(row)
            hint = QAbstractItemView.PositionAtBottom if last else QAbstractItemView.PositionAtTop
            self.scrollToItem(item, hint)
            return True
        return False

    def _select_entry_by_name(self, name: str) -> bool:
        target = (name or '').casefold()
        if not target:
            return False
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is None or item.text().casefold() != target:
                continue
            self.setCurrentCell(row, 0)
            self.selectRow(row)
            self.scrollToItem(item, QAbstractItemView.PositionAtCenter)
            return True
        return False

    def _select_pending_entry(self) -> None:
        if self._pending_select_name and self._select_entry_by_name(self._pending_select_name):
            self._pending_select_name = ''

    def _start_inline_rename(self, on_commit: Callable[[str, str], None]) -> None:
        selected_rows = sorted({idx.row() for idx in self.selectedIndexes()})
        if len(selected_rows) != 1:
            return
        item = self.item(selected_rows[0], 0)
        if item is None or item.text() == '..':
            return

        self._close_inline_rename()
        old_name = item.text()
        entry_type = str(item.data(Qt.UserRole) or 'file')
        edit = _InlineRenameEdit(old_name, entry_type, self)
        self._inline_rename_edit = edit
        self._inline_rename_item = item
        self.setCellWidget(item.row(), 0, edit)
        edit.rename_cancelled.connect(self._close_inline_rename)
        edit.rename_committed.connect(
            lambda new_name, old=old_name: self._commit_inline_rename(
                old,
                new_name,
                on_commit,
            )
        )
        QTimer.singleShot(0, edit.setFocus)

    def _commit_inline_rename(
        self,
        old_name: str,
        new_name: str,
        on_commit: Callable[[str, str], None],
    ) -> None:
        new_name = new_name.strip()
        self._close_inline_rename()
        if not new_name or new_name == old_name:
            return
        on_commit(old_name, new_name)

    def _close_inline_rename(self) -> None:
        edit = self._inline_rename_edit
        item = self._inline_rename_item
        self._inline_rename_edit = None
        self._inline_rename_item = None
        if item is not None and item.tableWidget() is self and item.row() >= 0:
            self.removeCellWidget(item.row(), 0)
        if edit is not None:
            edit.deleteLater()

    def _context_menu_row_at(self, pos) -> Optional[int]:
        index = self.indexAt(pos)
        if index.isValid():
            row = index.row()
            return row if not self.isRowHidden(row) else None

        row = self._visible_row_at_y(pos.y())
        if row is not None and self._pos_within_row_cells(row, pos.x()):
            return row

        viewport_pos = self.viewport().mapFrom(self, pos)
        if viewport_pos != pos:
            index = self.indexAt(viewport_pos)
            if index.isValid():
                row = index.row()
                return row if not self.isRowHidden(row) else None
            row = self._visible_row_at_y(viewport_pos.y())
            if row is not None and self._pos_within_row_cells(row, viewport_pos.x()):
                return row
        return None

    def _sync_context_menu_selection(self, pos) -> bool:
        row = self._context_menu_row_at(pos)
        if row is None:
            return False
        if any(selected.row() == row for selected in self.selectedIndexes()):
            return True
        first_index = self.model().index(row, 0)
        self.setCurrentIndex(first_index)
        self.selectionModel().select(first_index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        return True

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
        return self._visible_row_at_y(pos.y()) is None or pos.x() > last_rect.right()

    def _visible_row_at_y(self, y: int) -> Optional[int]:
        for row in range(self.rowCount()):
            if self.isRowHidden(row):
                continue
            top = self.rowViewportPosition(row)
            bottom = top + self.rowHeight(row)
            if top <= y < bottom:
                return row
        return None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            row = self._visible_row_at_y(event.pos().y())
            if row is not None and self._pos_within_row_cells(row, event.pos().x()):
                if self._activate_directory_row(row):
                    event.accept()
                    return
            elif self._can_go_to_parent():
                self._go_to_parent()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._sync_context_menu_selection(event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._sync_context_menu_selection(event.pos())
        super().contextMenuEvent(event)

    def _pos_within_row_cells(self, row: int, x: int) -> bool:
        last_col = self.columnCount() - 1
        if last_col < 0:
            return False
        rect = self.visualRect(self.model().index(row, last_col))
        return 0 <= x <= rect.right()

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
        self._apply_filter_to_rows()
        self._emit_status_counts()

    def _append_entry_row(
        self,
        name: str,
        entry_type: str,
        size: int,
        mtime: float,
        perm: str = '',
        *,
        is_parent: bool = False,
        mode: int = 0,
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
        name_item.setData(PERMISSION_MODE, int(mode))
        self.setItem(row, 0, name_item)
        self.setItem(row, 1, _make_sort_item('' if entry_type == 'dir' else _format_size(size)))
        self.setItem(row, 2, _make_sort_item(_format_mtime(mtime) if mtime > 0 else ''))
        if self.columnCount() > 3:
            self.setItem(row, 3, _make_sort_item(perm))

    def current_path(self) -> str:
        return self._current_path

    def filter_text(self) -> str:
        return self._filter_text

    def set_filter_text(self, text: str) -> None:
        if self._filter_text == text:
            return
        self._filter_text = text
        self._apply_filter_to_rows()
        self._emit_status_counts()
        self.filter_text_changed.emit(self._filter_text)

    def clear_filter(self) -> None:
        if not self._filter_text:
            self.filter_text_changed.emit('')
            self.filter_cancelled.emit()
            return
        self._filter_text = ''
        self._apply_filter_to_rows()
        self._emit_status_counts()
        self.filter_text_changed.emit('')
        self.filter_cancelled.emit()

    def _entry_matches_filter(self, name: str) -> bool:
        pattern = self._filter_text.casefold()
        if not pattern or name == '..':
            return True
        target = name.casefold()
        if _wildcard_filter_match(pattern, target):
            return True
        return any(
            _wildcard_filter_match(pattern, pinyin_target)
            for pinyin_target in _pinyin_initial_filter_targets(name)
        )

    def _apply_filter_to_rows(self) -> None:
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is None:
                continue
            self.setRowHidden(row, not self._entry_matches_filter(item.text()))

    def _emit_status_counts(self) -> None:
        selected_rows = {idx.row() for idx in self.selectedIndexes()}
        selected_files = 0
        selected_dirs = 0
        total_files = 0
        total_dirs = 0
        for row in range(self.rowCount()):
            if self.isRowHidden(row):
                continue
            item = self.item(row, 0)
            if item is None or item.text() == '..':
                continue
            entry_type = item.data(Qt.UserRole) or 'file'
            if entry_type == 'dir':
                total_dirs += 1
                if row in selected_rows:
                    selected_dirs += 1
            else:
                total_files += 1
                if row in selected_rows:
                    selected_files += 1
        self.status_counts_changed.emit(selected_files, total_files, selected_dirs, total_dirs)

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        self._activate_directory_row(row)

    def _activate_directory_row(self, row: int) -> bool:
        item = self.item(row, 0)
        if item is None:
            return False
        entry_type = item.data(Qt.UserRole)
        name = item.text()
        if entry_type == 'dir':
            self._enter_directory(name)
            return True
        return False

    def _activate_current_directory(self) -> bool:
        selected_rows = sorted({idx.row() for idx in self.selectedIndexes()})
        if len(selected_rows) != 1:
            return False
        row = selected_rows[0]
        if row != self.currentRow():
            return False
        return self._activate_directory_row(row)

    def _enter_directory(self, name: str) -> None:
        raise NotImplementedError

    def _handle_delete_key(self, *, permanent: bool) -> bool:
        return False
