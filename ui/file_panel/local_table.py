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

class LocalFileTable(_BaseFileTable):
    upload_requested = pyqtSignal(list)
    PEER_FOCUS_KEY = Qt.Key_Right

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

    def _go_to_root(self) -> None:
        current = os.path.abspath(os.path.normpath(self._current_path))
        root = _windows_drive_root(current) if sys.platform == 'win32' else '/'
        self.set_path(root)

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

    def _selected_file_paths(
        self,
        selected: list[tuple[str, str]] | None = None,
    ) -> list[str]:
        entries = selected or self._selected_entries()
        return [
            self._local_full_path(name)
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
        add_menu_key(menu, menu.addAction(tr('file.refresh'), self.refresh), Qt.Key_R)
        add_menu_key(menu, menu.addAction(tr('file.mkdir'), self._mkdir), Qt.Key_N)
        selected = self._selected_entries() if has_context_row else []
        if selected:
            menu.addSeparator()
            file_paths = self._selected_file_paths(selected)
            if file_paths:
                add_menu_key(
                    menu,
                    menu.addAction(
                        tr('file.edit_system'),
                        lambda: self.edit_requested.emit(file_paths, False),
                    ),
                    Qt.Key_F3,
                )
                add_menu_key(
                    menu,
                    menu.addAction(
                        tr('file.edit_configured'),
                        lambda: self.edit_requested.emit(file_paths, True),
                    ),
                    Qt.Key_F4,
                )
                menu.addSeparator()
            add_menu_key(menu, menu.addAction(tr('file.copy_name'), lambda: self._copy_selected_names(selected)), Qt.Key_C)
            add_menu_key(menu, menu.addAction(tr('file.copy_path'), lambda: self._copy_selected_paths(selected)), Qt.Key_A)
            add_menu_key(menu, menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path), Qt.Key_P)
            menu.addSeparator()
            add_menu_key(
                menu,
                menu.addAction(tr('file.upload_to_right_dir'), lambda: self._upload_selected(selected)),
                Qt.Key_T,
            )
            if len(selected) == 1:
                add_menu_key(menu, menu.addAction(tr('file.rename'), self._rename), Qt.Key_F2)
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
            add_menu_key(menu, menu.addAction(tr('file.copy_parent_path'), self._copy_current_directory_path), Qt.Key_P)
        exec_menu(menu, self.viewport().mapToGlobal(pos))

    def _request_refresh(self) -> None:
        self.refresh()

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

    def _copy_selected_names(self, selected: list[tuple[str, str]] | None = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(name for name, _ in selected))

    def _copy_selected_paths(self, selected: list[tuple[str, str]] | None = None) -> None:
        selected = selected or self._selected_entries()
        if not selected:
            return
        self._copy_text('\n'.join(self._local_full_path(name) for name, _ in selected))

    def _copy_current_directory_path(self) -> None:
        self._copy_text(self._current_path)

    def _upload_selected(self, selected: list[tuple[str, str]] | None = None) -> None:
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
            self._select_pending_entry()
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
        self._prepare_path_change_selection()
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
        self._prepare_path_change_selection()
        self.clear_filter()
        self.path_changed.emit(self._current_path)
        self.refresh()

def _list_windows_drives() -> list[str]:
    if sys.platform != 'win32':
        return []
    import ctypes

    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    drives: list[str] = []
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
