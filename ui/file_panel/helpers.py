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


"""Shared helpers for file panel tables and layouts."""

def _rename_selection_length(name: str) -> int:
    """Return the stem length to select for an inline rename editor."""
    if not name or name.startswith('.'):
        return len(name)
    lower_name = name.casefold()
    for suffix in sorted(_COMMON_RENAME_SUFFIXES, key=len, reverse=True):
        if lower_name.endswith(suffix) and len(name) > len(suffix):
            return len(name) - len(suffix)
    return len(name)


def _apply_local_permission_change(
    paths: Sequence[str],
    change: PermissionChange,
    progress: Callable[[int, int, int], None],
) -> tuple[int, int]:
    """Apply permissions in a worker thread, returning (processed, failed)."""
    targets: list[str] = []
    for path in dict.fromkeys(paths):
        if change.recursive and os.path.isdir(path) and not os.path.islink(path):
            nested_dirs: list[str] = []
            for root, dir_names, file_names in os.walk(path, topdown=True, followlinks=False):
                dir_names[:] = [name for name in dir_names if not os.path.islink(os.path.join(root, name))]
                targets.extend(
                    os.path.join(root, name)
                    for name in file_names
                    if not os.path.islink(os.path.join(root, name))
                )
                nested_dirs.extend(os.path.join(root, name) for name in dir_names)
            targets.extend(reversed(nested_dirs))
        targets.append(path)

    targets = list(dict.fromkeys(targets))
    total = len(targets)
    failed = 0
    progress(0, total, failed)
    for done, target in enumerate(targets, start=1):
        try:
            current = os.lstat(target).st_mode
            new_mode = change.apply(current)
            if os.path.islink(target):
                os.chmod(target, new_mode, follow_symlinks=False)
            else:
                os.chmod(target, new_mode)
        except (OSError, NotImplementedError):
            failed += 1
        progress(done, total, failed)
    return total, failed


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


def _is_cjk(char: str) -> bool:
    return '\u4e00' <= char <= '\u9fff'


def _wildcard_filter_match(pattern: str, target: str) -> bool:
    parts = [part for part in pattern.split('*') if part]
    if not parts:
        return True
    pos = 0
    if not pattern.startswith('*'):
        first = parts[0]
        if not target.startswith(first):
            return False
        pos = len(first)
        parts = parts[1:]
    for part in parts:
        found = target.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    return True


def _pinyin_initial_filter_targets(name: str) -> list[str]:
    if lazy_pinyin is None or pinyin is None or Style is None:
        return []
    base = ''.join(lazy_pinyin(
        name,
        style=Style.FIRST_LETTER,
        errors=lambda chars: list(chars),
    )).casefold()
    targets = [base]
    if not name or not _is_cjk(name[0]):
        return targets
    if len(name) > 1 and _is_cjk(name[1]):
        return targets
    first_variants = pinyin(name[0], style=Style.FIRST_LETTER, heteronym=True)
    initials = sorted({item.casefold() for group in first_variants for item in group if item})
    for initial in initials:
        candidate = f'{initial}{base[1:]}'
        if candidate not in targets:
            targets.append(candidate)
    return targets


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


class _InlineRenameEdit(QLineEdit):
    """Name-cell editor which commits once on Enter/focus loss or cancels on Esc."""

    rename_committed = pyqtSignal(str)
    rename_cancelled = pyqtSignal()

    def __init__(self, name: str, entry_type: str, parent: QWidget = None) -> None:
        super().__init__(name, parent)
        self.setObjectName('tableCellEditor')
        self._finished = False
        if entry_type == 'file':
            self.setSelection(0, _rename_selection_length(name))
        else:
            self.selectAll()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self._finish(commit=False)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish(commit=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self._finish(commit=True)
        super().focusOutEvent(event)

    def _finish(self, *, commit: bool) -> None:
        if self._finished:
            return
        self._finished = True
        if commit:
            self.rename_committed.emit(self.text())
        else:
            self.rename_cancelled.emit()


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
