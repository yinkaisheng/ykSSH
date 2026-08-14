#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

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


from ui.file_panel.base_table import _BaseFileTable

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
        self._list_callback: Callable[[str], Any] | None = None
        self._download_directory_provider: Callable[[], str] | None = None
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

    def _selected_file_paths(
        self,
        selected: list[tuple[str, str]] | None = None,
    ) -> list[str]:
        entries = selected or self._selected_entries()
        return [
            self._remote_full_path(name)
            for name, entry_type in entries
            if entry_type != 'dir'
        ]

    def _edit_selected(self, *, use_configured_editor: bool) -> bool:
        paths = self._selected_file_paths()
        if not paths:
            return False
        self.edit_requested.emit(paths, use_configured_editor)
        return True

    def _show_context_menu(self, pos) -> None:
        has_context_row = self._sync_context_menu_selection(pos)
        menu = ShortcutMenu(self)
        add_menu_key(menu, menu.addAction(tr('file.refresh'), self.refresh_requested.emit), Qt.Key_R)
        add_menu_key(menu, menu.addAction(tr('file.mkdir'), self._mkdir), Qt.Key_N)
        selected = self._selected_entries() if has_context_row else []
        if selected:
            menu.addSeparator()
            file_paths = self._selected_file_paths(selected)
            if file_paths:
                menu.addAction(
                    tr('file.edit_system'),
                    lambda: self.edit_requested.emit(file_paths, False),
                )
                menu.addAction(
                    tr('file.edit_configured'),
                    lambda: self.edit_requested.emit(file_paths, True),
                )
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

    def _copy_selected_names(self, selected: list[tuple[str, str]] | None = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(name for name, _ in selected))

    def _copy_selected_paths(self, selected: list[tuple[str, str]] | None = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(self._remote_full_path(name) for name, _ in selected))

    def _copy_current_directory_path(self) -> None:
        base = self._current_path.rstrip('/')
        self._copy_text(base if base else '/')

    def _download_selected(self, selected: list[tuple[str, str]] | None = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        paths = [self._remote_full_path(name) for name, _ in selected]
        logger.info(f'Remote download selection: count={len(paths)}, paths={paths}')
        self.download_requested.emit(paths)

    def _download_selected_to_other(self, selected: list[tuple[str, str]] | None = None) -> None:
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

    def set_download_directory_provider(self, provider: Callable[[], str] | None) -> None:
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
