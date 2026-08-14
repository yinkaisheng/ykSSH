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
    _FILE_PANEL_TOOLBAR_TABLE_SPACING,
    EqualSplitSplitter,
    _apply_column_widths,
    _apply_file_panel_toolbar_layout,
    _file_table_header_labels,
)
from ui.file_panel.local_table import LocalFileTable
from ui.file_panel.remote_table import RemoteFileTable, _remote_parent_and_name
from ui.file_panel.widgets import (
    _FileNavToolbar,
    _FilePanelStatusBar,
    _show_favorites_menu,
)

class LocalFilePanel(QWidget):
    """Local path bar + local file table."""

    path_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Callable[[bool, dict[str, int]], None] | None = None,
        initial_path: str = '',
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._initial_path = initial_path
        self._favorites_provider: (
            Callable[[], tuple[Sequence[FavoritePath], Sequence[FavoritePath]]] | None
        ) = None
        self._manage_favorites_handler: Callable[[], None] | None = None
        self._favorites_changed_handler: Callable[[], None] | None = None
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

        self.table = LocalFileTable(initial_path=self._initial_path)
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
        self.statusbar = _FilePanelStatusBar(self.table, transfer_kind='upload')
        layout.addWidget(self.statusbar)

        self.table.path_changed.connect(self.path_edit.setText)
        self.table.path_changed.connect(self.path_changed.emit)
        self.table.path_changed.connect(lambda _path: self._nav_toolbar.retranslate_ui())
        self.path_edit.returnPressed.connect(self._path_entered)
        self._nav_toolbar.refresh_requested.connect(self.table.refresh)
        self._nav_toolbar.favorites_requested.connect(self._show_favorites_menu)
        self.table.favorites_menu_requested.connect(self._show_favorites_menu)
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

    def set_favorites_changed_handler(self, handler: Callable[[], None]) -> None:
        self._favorites_changed_handler = handler

    def set_sftp_handler(self, handler) -> None:
        if self._sftp_handler is not None:
            try:
                self.table.upload_requested.disconnect()
                self._sftp_handler.transfer_status_changed.disconnect(
                    self.statusbar.set_transfer_status,
                )
            except TypeError:
                pass
        self._sftp_handler = handler
        if handler is not None:
            self.table.upload_requested.connect(handler.upload_local_paths)
            handler.transfer_status_changed.connect(self.statusbar.set_transfer_status)

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
            on_navigate=self._navigate_favorite,
        )

    def _navigate_favorite(self, entry: FavoritePath) -> None:
        path = entry.path
        if os.path.exists(path):
            is_file = os.path.isfile(path)
            if entry.is_file != is_file:
                entry.is_file = is_file
                if self._favorites_changed_handler is not None:
                    self._favorites_changed_handler()
        self.set_path(path)

    def _setup_table_header_menu(self, *, is_local: bool) -> None:
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(
            lambda pos, local=is_local: self._show_table_header_menu(local, pos),
        )

    def _show_table_header_menu(self, is_local: bool, pos) -> None:
        menu = ShortcutMenu(self)
        save_action = menu.addAction(tr('file.save_column_widths'))
        add_menu_key(menu, save_action, Qt.Key_S)
        chosen = exec_menu(menu, self.table.horizontalHeader().mapToGlobal(pos))
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
        self.statusbar.apply_layout()

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
        self.table._emit_status_counts()


class RemoteFilePanel(QWidget):
    """Remote path bar + remote file table (or not-connected placeholder)."""

    path_changed = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Callable[[bool, dict[str, int]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._sftp_handler = None
        self._favorites_provider: Callable[[], Sequence[FavoritePath]] | None = None
        self._manage_favorites_handler: Callable[[], None] | None = None
        self._favorites_changed_handler: Callable[[], None] | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._build_ui()
        tasks = self._background_tasks
        self.destroyed.connect(
            lambda _obj=None: [task.cancel() for task in list(tasks) if not task.done()]
        )

    def _track_background_task(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)

        def _done(done: asyncio.Task) -> None:
            self._background_tasks.discard(done)
            if done.cancelled():
                return
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                logger.error(f'Remote file panel task failed: {error}')

        task.add_done_callback(_done)

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
        self.statusbar = _FilePanelStatusBar(self.table, transfer_kind='download')
        layout.addWidget(self.statusbar)

        self.table.path_changed.connect(self.path_edit.setText)
        self.table.path_changed.connect(self.path_changed.emit)
        self.table.path_changed.connect(lambda _path: self._nav_toolbar.retranslate_ui())
        self.path_edit.returnPressed.connect(self._path_entered)
        self._nav_toolbar.refresh_requested.connect(self._remote_refresh)
        self._nav_toolbar.favorites_requested.connect(self._show_favorites_menu)
        self.table.favorites_menu_requested.connect(self._show_favorites_menu)
        self._setup_table_header_menu(is_local=False)

    def set_favorites_provider(self, provider: Callable[[], Sequence[FavoritePath]]) -> None:
        """Provide session remote favorites."""
        self._favorites_provider = provider

    def set_manage_favorites_handler(self, handler: Callable[[], None]) -> None:
        self._manage_favorites_handler = handler

    def set_favorites_changed_handler(self, handler: Callable[[], None]) -> None:
        self._favorites_changed_handler = handler

    def _show_favorites_menu(self) -> None:
        entries: Sequence[FavoritePath] = ()
        if self._favorites_provider is not None:
            entries = self._favorites_provider()
        _show_favorites_menu(
            self,
            self._nav_toolbar.favorites_button(),
            sections=((tr('file.favorites.session_remote'), entries),),
            on_manage=self._manage_favorites_handler,
            on_navigate=self._navigate_favorite,
        )

    def _navigate_favorite(self, entry: FavoritePath) -> None:
        if self._sftp_handler is None:
            self.table.set_path(entry.path)
            return
        self._track_background_task(asyncio.create_task(self._navigate_favorite_async(entry)))

    async def _navigate_favorite_async(self, entry: FavoritePath) -> None:
        try:
            directory, select_name, is_file = await self._sftp_handler.resolve_remote_navigation_target(entry.path)
        except Exception as exc:
            logger.warning(f'Remote favorite path resolve failed: path={entry.path}, error={exc}')
            self.table.set_path(entry.path)
            return
        if is_file is not None and entry.is_file != is_file:
            entry.is_file = is_file
            if self._favorites_changed_handler is not None:
                self._favorites_changed_handler()
        self.table.set_path(directory, select_name=select_name)

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
        menu = ShortcutMenu(self)
        save_action = menu.addAction(tr('file.save_column_widths'))
        add_menu_key(menu, save_action, Qt.Key_S)
        chosen = exec_menu(menu, self.table.horizontalHeader().mapToGlobal(pos))
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
        if self._sftp_handler is None:
            self.table.set_path(path)
            return
        self._track_background_task(asyncio.create_task(self._set_path_resolved_async(path)))

    async def _set_path_resolved_async(self, path: str) -> None:
        try:
            directory, select_name, _is_file = await self._sftp_handler.resolve_remote_navigation_target(path)
        except Exception as exc:
            logger.warning(f'Remote favorite path resolve failed: path={path}, error={exc}')
            self.table.set_path(path)
            return
        self.table.set_path(directory, select_name=select_name)

    def refresh(self) -> None:
        self.table.refresh()

    def apply_toolbar_layout(self) -> None:
        _apply_file_panel_toolbar_layout(
            self._toolbar,
            label=self._label,
            path_edit=self.path_edit,
            nav_toolbar=self._nav_toolbar,
        )
        self.statusbar.apply_layout()

    def apply_column_widths(self, widths: dict[str, int]) -> None:
        _apply_column_widths(
            self.table,
            FILE_TABLE_COLUMNS,
            widths,
            defaults=DEFAULT_REMOTE_COLUMN_WIDTHS,
        )

    def set_list_callback(self, callback: Callable[[str], list[dict]]) -> None:
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
                self.table.download_to_requested.disconnect()
                self.table.delete_requested.disconnect()
                self.table.rename_requested.disconnect()
                self.table.properties_requested.disconnect()
                self.table.mkdir_requested.disconnect()
                self.table.refresh_requested.disconnect()
                self._sftp_handler.transfer_status_changed.disconnect(
                    self.statusbar.set_transfer_status,
                )
                self._sftp_handler.property_status_changed.disconnect(
                    self.statusbar.set_property_status,
                )
            except TypeError:
                pass
        self._sftp_handler = handler
        if handler is None:
            self.table.set_download_directory_provider(None)
            return
        self.table.upload_requested.connect(handler.upload_local_paths)
        self.table.download_requested.connect(handler.download_remote_paths)
        self.table.download_to_requested.connect(handler.download_remote_paths_to)
        self.table.delete_requested.connect(handler.delete_remote_paths)
        self.table.rename_requested.connect(handler.rename_remote)
        self.table.properties_requested.connect(handler.apply_remote_properties)
        self.table.mkdir_requested.connect(handler.mkdir_remote)
        self.table.set_download_directory_provider(lambda: handler.local_dir)
        handler.transfer_status_changed.connect(self.statusbar.set_transfer_status)
        handler.property_status_changed.connect(self.statusbar.set_property_status)
        self.table.refresh_requested.connect(
            lambda: handler.refresh_remote(self.table.current_path()),
        )

    def retranslate_ui(self) -> None:
        self._label.setText(tr('file.remote'))
        self._nav_toolbar.retranslate_ui()
        self.placeholder.setText(tr('file.not_connected'))
        self.path_edit.setPlaceholderText(tr('file.path_placeholder'))
        self.table.setHorizontalHeaderLabels(_file_table_header_labels())
        self.table._emit_status_counts()


class FilesPanel(QWidget):
    """One tab's file panel: local | splitter | remote."""

    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_save_column_widths: Callable[[bool, dict[str, int]], None] | None = None,
        initial_local_path: str = '',
    ) -> None:
        super().__init__(parent)
        self._on_save_column_widths = on_save_column_widths
        self._initial_local_path = initial_local_path
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
            initial_path=self._initial_local_path,
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

    def create_panel(self, tab_id: str, *, initial_local_path: str = '') -> FilesPanel:
        panel = self._panels.get(tab_id)
        if panel is not None:
            return panel
        panel = FilesPanel(
            on_save_column_widths=self._on_save_column_widths,
            initial_local_path=initial_local_path,
        )
        self._panels[tab_id] = panel
        self._stack.addWidget(panel)
        return panel

    def get_panel(self, tab_id: str) -> FilesPanel | None:
        return self._panels.get(tab_id)

    def show_panel(self, tab_id: str) -> FilesPanel | None:
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
            try:
                save_file_panel_column_widths(local_column_widths=widths)
            except OSError as exc:
                logger.warning(f'Failed to save local file column widths: {exc}')
                message_warning(self, tr('storage.save_failed_title'), tr('storage.save_config_failed'))
                return
            for panel in self._panels.values():
                panel.local_file_panel.apply_column_widths(widths)
            return
        try:
            save_file_panel_column_widths(remote_column_widths=widths)
        except OSError as exc:
            logger.warning(f'Failed to save remote file column widths: {exc}')
            message_warning(self, tr('storage.save_failed_title'), tr('storage.save_config_failed'))
            return
        for panel in self._panels.values():
            panel.remote_file_panel.apply_column_widths(widths)

    def retranslate_ui(self) -> None:
        for panel in self._panels.values():
            panel.retranslate_ui()

    def apply_file_panel_layout(self) -> None:
        for panel in self._panels.values():
            panel.apply_file_panel_layout()
