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


class LocalFileTable(_BaseFileTable):
    upload_requested = pyqtSignal(list)

    def __init__(self, parent: QWidget = None, *, initial_path: str = '') -> None:
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
        self._current_path = initial_path or os.path.expanduser('~')
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._property_tasks: set[asyncio.Task] = set()

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
        has_context_row = self._sync_context_menu_selection(pos)
        menu = ShortcutMenu(self)
        add_menu_key(menu, menu.addAction(tr('file.refresh'), self.refresh), Qt.Key_R)
        add_menu_key(menu, menu.addAction(tr('file.mkdir'), self._mkdir), Qt.Key_N)
        selected = self._selected_entries() if has_context_row else []
        if selected:
            menu.addSeparator()
            add_menu_key(menu, menu.addAction(tr('file.copy_name'), lambda: self._copy_selected_names(selected)), Qt.Key_C)
            add_menu_key(menu, menu.addAction(tr('file.copy_path'), lambda: self._copy_selected_paths(selected)), Qt.Key_P)
            add_menu_key(menu, menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path), Qt.Key_L)
            menu.addSeparator()
            add_menu_key(
                menu,
                menu.addAction(tr('file.upload_to_right_dir'), lambda: self._upload_selected(selected)),
                Qt.Key_T,
            )
            if len(selected) == 1:
                add_menu_key(menu, menu.addAction(tr('file.rename'), self._rename), Qt.Key_E)
            add_menu_key(menu, menu.addAction(tr('file.properties'), self._properties), Qt.Key_O)
            shift_held = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
            if shift_held:
                add_menu_key(
                    menu,
                    menu.addAction(
                        tr('file.delete_permanently'),
                        lambda: self._delete_selected(permanent=True),
                    ),
                    Qt.Key_D,
                )
            else:
                add_menu_key(
                    menu,
                    menu.addAction(
                        tr('file.move_to_trash'),
                        lambda: self._delete_selected(permanent=False),
                    ),
                    Qt.Key_D,
                )
        else:
            menu.addSeparator()
            add_menu_key(menu, menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path), Qt.Key_L)
        exec_menu(menu, self.viewport().mapToGlobal(pos))

    def _mkdir(self) -> None:
        name = prompt_text(self, tr('file.mkdir'), tr('file.prompt_name'))
        if not name:
            return
        path = self._local_full_path(name)
        try:
            os.mkdir(path)
            logger.info(f'Local mkdir done: path={path}')
        except OSError as exc:
            logger.warning(f'Local mkdir failed: path={path}, error={exc}')
            return
        self.refresh()

    def _rename(self) -> None:
        self._start_inline_rename(self._rename_local)

    def _rename_local(self, old_name: str, new_name: str) -> None:
        old_path = self._local_full_path(old_name)
        new_path = self._local_full_path(new_name)
        try:
            os.rename(old_path, new_path)
            logger.info(f'Local rename done: old={old_path}, new={new_path}')
        except OSError as exc:
            logger.warning(f'Local rename failed: old={old_path}, new={new_path}, error={exc}')
            return
        self._pending_select_name = new_name
        self.refresh()

    def _properties(self) -> None:
        selected = self._selected_entries()
        if not selected:
            return
        paths = [self._local_full_path(name) for name, _entry_type in selected]
        try:
            stats = [os.lstat(path) for path in paths]
        except OSError as exc:
            logger.warning(f'Local properties stat failed: error={exc}')
            return
        modes = [item_stat.st_mode for item_stat in stats]
        file_count = sum(1 for _name, entry_type in selected if entry_type != 'dir')
        total_file_bytes = sum(
            item_stat.st_size
            for item_stat, (_name, entry_type) in zip(stats, selected)
            if entry_type != 'dir'
        )
        dialog = FilePropertiesDialog(
            modes,
            has_directories=any(entry_type == 'dir' for _name, entry_type in selected),
            windows_local=sys.platform == 'win32',
            file_count=file_count,
            total_file_bytes=total_file_bytes,
            parent=self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        task = asyncio.create_task(self._apply_properties_async(paths, dialog.permission_change()))
        self._property_tasks.add(task)
        task.add_done_callback(self._property_task_done)

    def _property_task_done(self, task: asyncio.Task) -> None:
        self._property_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(f'Local properties task failed: error={error}')

    async def _apply_properties_async(self, paths: Sequence[str], change: PermissionChange) -> None:
        self.property_status_changed.emit(True, 0, 0, 0)
        try:
            _done, failed = await asyncio.to_thread(
                _apply_local_permission_change,
                paths,
                change,
                lambda done, total, failed: self.property_status_changed.emit(
                    True, done, total, failed,
                ),
            )
            if failed:
                message_warning(
                    self,
                    tr('file.properties'),
                    tr('file.properties.failed', count=failed),
                )
        finally:
            self.property_status_changed.emit(False, 0, 0, 0)
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
        paths = [self._local_full_path(name) for name, _ in selected]
        logger.info(f'Local upload selection: count={len(paths)}, paths={paths}')
        self.upload_requested.emit(paths)

    def _delete_selected(self, *, permanent: bool = False) -> None:
        selected = self._selected_entries()
        if not selected:
            return
        if permanent:
            if not ask_yes_no(
                self,
                tr('file.delete_permanently'),
                tr('file.confirm_delete_permanently'),
            ):
                return
        self._delete_entries(selected, permanent=permanent)

    def _handle_delete_key(self, *, permanent: bool) -> bool:
        selected = self._selected_entries()
        if not selected:
            return False
        self._delete_entries(selected, permanent=permanent)
        return True

    def _delete_entries(self, selected: list[tuple[str, str]], *, permanent: bool) -> None:
        logger.info(
            'Local delete batch start: '
            f'count={len(selected)}, permanent={permanent}, base={self._current_path}'
        )
        for name, entry_type in selected:
            path = self._local_full_path(name)
            try:
                if not permanent:
                    if not QFile.moveToTrash(path):
                        logger.warning(f'Local move to trash failed: path={path}')
                        message_warning(
                            self,
                            tr('file.move_to_trash'),
                            tr('file.move_to_trash_failed', path=path),
                        )
                    else:
                        logger.info(f'Local move to trash done: path={path}')
                elif entry_type == 'dir':
                    shutil.rmtree(path)
                    logger.info(f'Local delete directory done: path={path}')
                else:
                    os.remove(path)
                    logger.info(f'Local delete file done: path={path}')
            except OSError as exc:
                logger.warning(f'Local delete failed: path={path}, permanent={permanent}, error={exc}')
                continue
        self.refresh()
        logger.info(
            'Local delete batch done: '
            f'count={len(selected)}, permanent={permanent}, base={self._current_path}'
        )

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

        dirs: list[tuple[str, int, float, str, int]] = []
        files: list[tuple[str, int, float, str, int]] = []
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
                dirs.append((name, st.st_size, st.st_mtime, perm, st.st_mode))
            else:
                files.append((name, st.st_size, st.st_mtime, perm, st.st_mode))

        dirs.sort(key=lambda item: item[0].casefold())
        files.sort(key=lambda item: item[0].casefold())
        for name, size, mtime, perm, mode in dirs:
            self._append_entry_row(name, 'dir', size, mtime, perm, mode=mode)
        for name, size, mtime, perm, mode in files:
            self._append_entry_row(name, 'file', size, mtime, perm, mode=mode)
        self._end_refresh(sort_column, sort_order)
        self._select_pending_entry()

    def _enter_directory(self, name: str) -> None:
        if name == '..':
            parent = os.path.dirname(self._current_path.rstrip(os.sep))
            self._current_path = parent or self._current_path
        else:
            self._current_path = os.path.join(self._current_path, name)
        self.clear_filter()
        self.path_changed.emit(self._current_path)
        self.refresh()

    def set_path(self, path: str) -> None:
        candidate = os.path.normpath(os.path.expanduser((path or '').strip()))
        select_name = ''
        if candidate and os.path.isfile(candidate):
            select_name = os.path.basename(candidate)
            candidate = os.path.dirname(candidate) or self._current_path
        self._current_path = candidate or os.path.expanduser('~')
        self._pending_select_name = select_name
        self.clear_filter()
        self.path_changed.emit(self._current_path)
        self.refresh()


class RemoteFileTable(_BaseFileTable):
    upload_requested = pyqtSignal(list)
    download_requested = pyqtSignal(list)
    download_to_requested = pyqtSignal(list, str)
    delete_requested = pyqtSignal(list)
    rename_requested = pyqtSignal(str, str)
    mkdir_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    properties_requested = pyqtSignal(list, object)

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
        self._download_directory_provider: Optional[Callable[[], str]] = None
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
        has_context_row = self._sync_context_menu_selection(pos)
        menu = ShortcutMenu(self)
        add_menu_key(menu, menu.addAction(tr('file.refresh'), self.refresh_requested.emit), Qt.Key_R)
        add_menu_key(menu, menu.addAction(tr('file.mkdir'), self._mkdir), Qt.Key_N)
        selected = self._selected_entries() if has_context_row else []
        if selected:
            menu.addSeparator()
            add_menu_key(menu, menu.addAction(tr('file.copy_name'), lambda: self._copy_selected_names(selected)), Qt.Key_C)
            add_menu_key(menu, menu.addAction(tr('file.copy_path'), lambda: self._copy_selected_paths(selected)), Qt.Key_P)
            add_menu_key(menu, menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path), Qt.Key_L)
            menu.addSeparator()
            add_menu_key(
                menu,
                menu.addAction(tr('file.download_to_left_dir'), lambda: self._download_selected(selected)),
                Qt.Key_T,
            )
            add_menu_key(
                menu,
                menu.addAction(tr('file.download_to_other'), lambda: self._download_selected_to_other(selected)),
                Qt.Key_B,
            )
            if len(selected) == 1:
                add_menu_key(menu, menu.addAction(tr('file.rename'), self._rename), Qt.Key_E)
            add_menu_key(menu, menu.addAction(tr('file.properties'), self._properties), Qt.Key_O)
            add_menu_key(menu, menu.addAction(tr('file.delete'), self._delete_selected), Qt.Key_D)
        else:
            menu.addSeparator()
            add_menu_key(menu, menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path), Qt.Key_L)
        exec_menu(menu, self.viewport().mapToGlobal(pos))

    def _mkdir(self) -> None:
        name = prompt_text(self, tr('file.mkdir'), tr('file.prompt_name'))
        if name:
            logger.info(f'Remote mkdir selection: current_path={self._current_path}, name={name}')
            self.mkdir_requested.emit(name)

    def _rename(self) -> None:
        self._start_inline_rename(self._rename_remote)

    def _rename_remote(self, old_name: str, new_name: str) -> None:
        logger.info(
            'Remote rename selection: '
            f'current_path={self._current_path}, old_name={old_name}, new_name={new_name}'
        )
        self._pending_select_name = new_name
        self.rename_requested.emit(old_name, new_name)

    def _properties(self) -> None:
        selected = self._selected_entries()
        if not selected:
            return
        modes: list[int] = []
        file_count = 0
        total_file_bytes = 0
        for name, entry_type in selected:
            items = self.findItems(name, Qt.MatchExactly)
            item = next((candidate for candidate in items if candidate.column() == 0), None)
            if item is None:
                return
            modes.append(int(item.data(PERMISSION_MODE) or 0))
            if entry_type != 'dir':
                file_count += 1
                total_file_bytes += int(item.data(SORT_SIZE) or 0)
        dialog = FilePropertiesDialog(
            modes,
            has_directories=any(entry_type == 'dir' for _name, entry_type in selected),
            file_count=file_count,
            total_file_bytes=total_file_bytes,
            parent=self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        paths = [self._remote_full_path(name) for name, _entry_type in selected]
        self.properties_requested.emit(paths, dialog.permission_change())

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
        paths = [self._remote_full_path(name) for name, _ in selected]
        logger.info(f'Remote download selection: count={len(paths)}, paths={paths}')
        self.download_requested.emit(paths)

    def _download_selected_to_other(self, selected: Optional[list[tuple[str, str]]] = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        directory = QFileDialog.getExistingDirectory(
            self,
            tr('file.select_download_dir'),
            self._download_directory() or os.path.expanduser('~'),
        )
        if not directory:
            return
        paths = [self._remote_full_path(name) for name, _ in selected]
        logger.info(
            'Remote download to other selection: '
            f'count={len(paths)}, local_dir={directory}, paths={paths}'
        )
        self.download_to_requested.emit(paths, directory)

    def _delete_selected(self) -> None:
        selected = self._selected_entries()
        if not selected:
            return
        if ask_yes_no(self, tr('file.delete'), tr('file.confirm_delete')):
            paths = [self._remote_full_path(name) for name, _ in selected]
            logger.info(f'Remote delete selection confirmed: count={len(paths)}, paths={paths}')
            self.delete_requested.emit(paths)

    def _handle_delete_key(self, *, permanent: bool) -> bool:
        selected = self._selected_entries()
        if not selected:
            return False
        if permanent or ask_yes_no(self, tr('file.delete'), tr('file.confirm_delete')):
            paths = [self._remote_full_path(name) for name, _ in selected]
            logger.info(
                'Remote delete key selection: '
                f'count={len(paths)}, permanent={permanent}, paths={paths}'
            )
            self.delete_requested.emit(paths)
        return True

    def set_list_callback(self, callback: Callable[[str], Any]) -> None:
        self._list_callback = callback

    def set_download_directory_provider(self, provider: Optional[Callable[[], str]]) -> None:
        self._download_directory_provider = provider

    def _download_directory(self) -> str:
        if self._download_directory_provider is None:
            return ''
        return self._download_directory_provider() or ''

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
            self._append_entry_row(
                name,
                entry_type,
                size,
                mtime,
                perm,
                mode=int(entry.get('mode', 0) or 0),
            )
        self._end_refresh(sort_column, sort_order)
        self._select_pending_entry()

    def _enter_directory(self, name: str) -> None:
        if name == '..':
            parent = os.path.dirname(self._current_path.rstrip('/'))
            self._current_path = parent or '/'
        else:
            base = self._current_path.rstrip('/')
            self._current_path = f'{base}/{name}' if base else f'/{name}'
        self.clear_filter()
        self.path_changed.emit(self._current_path)
        self.refresh_requested.emit()

    def set_path(self, path: str, *, select_name: str = '') -> None:
        path = path or '/'
        if select_name:
            self._current_path = path
            self._pending_select_name = select_name
        else:
            parent, name = _remote_parent_and_name(path)
            if name and self._remote_entry_is_file(parent, name):
                self._current_path = parent
                self._pending_select_name = name
            else:
                self._current_path = path
                self._pending_select_name = ''
        self.clear_filter()
        self.path_changed.emit(self._current_path)
        self.refresh_requested.emit()

    def _remote_entry_is_file(self, parent: str, name: str) -> bool:
        if self._list_callback is None:
            return False
        entries = self._list_callback(parent) or []
        target = name.casefold()
        for entry in entries:
            entry_name = str(entry.get('name', '') or '')
            if entry_name.casefold() == target:
                return not bool(entry.get('is_dir'))
        return False

    def clear_remote(self) -> None:
        self._list_callback = None
        self.setRowCount(0)
        self.clear_filter()
        self._emit_status_counts()


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


def _remote_parent_and_name(path: str) -> tuple[str, str]:
    text = (path or '').strip()
    if not text or text == '/':
        return '/', ''
    text = text.rstrip('/')
    parent, _, name = text.rpartition('/')
    if not parent:
        parent = '/'
    return parent, name
