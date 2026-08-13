#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import List, Optional, Sequence

from PyQt5.QtCore import Qt, QTimer, QPoint, QEvent
from PyQt5.QtGui import QCloseEvent, QMouseEvent, QResizeEvent, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFrame,
    QMainWindow,
    QSplitter,
    QSplitterHandle,
    QVBoxLayout,
    QWidget,
)

from core.connection_manager import ConnectionManager
from core.path_resolver import resolve_local_path
from core.sftp_ui_handler import SftpUiHandler
from core.ssh_session import HostKeyChangedError, HostKeyRejectedError
from i18n import register_retranslator, set_language, tr
from log_util import logger
from models.favorite_path import FavoritePath
from models.session_item import SessionItem
from storage.app_config import (
    DEFAULT_SESSION_TREE_WIDTH,
    get_app_config,
    save_app_preferences,
    save_file_panel_local_favorites,
    save_window_state,
)
from storage.credential_store import CredentialStore
from storage.session_profile_store import SessionProfileStore
from ui.about_dialog import show_about_dialog
from ui.favorites_dialog import (
    LocalFavoritesDialog,
    RemoteFavoritesDialog,
    show_local_favorites_dialog,
    show_remote_favorites_dialog,
)
from ui.settings_dialog import AppSettings, prompt_app_settings
from ui.file_table_panel import FilePanelsContainer, FilesPanel
from ui.side_panel import SidePanel
from ui.terminal_tab_widget import TerminalTabWidget
from ui.terminal_vt_widget import TerminalVTWidget
from ui.window_title_bar import WindowTitleBar
from ui.theme import (
    apply_app_theme,
    apply_main_window_border,
    apply_window_title_bar,
    get_theme_palette,
    normalize_terminal_font_family,
    normalize_terminal_font_size,
    normalize_theme_name,
)
from ui.dialog_i18n import ask_yes_no, ask_yes_no_async, message_warning


def splitter_ratio_to_sizes(total: int, ratio: float) -> list[int]:
    left = max(120, int(total * ratio))
    return [left, max(120, total - left)]


def splitter_sizes_to_ratio(sizes: list[int]) -> float:
    total = sum(sizes)
    if total <= 0:
        return 0.25
    return round(sizes[0] / total, 3)


class MainWindow(QMainWindow):
    DEFAULT_WIDTH = 1400
    DEFAULT_HEIGHT = 900
    MIN_TERMINAL_PANE_WIDTH = 120
    DEFAULT_VERTICAL_SPLITTER_RATIO = 0.65
    RESIZE_MARGIN = 5

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.profile_store = SessionProfileStore()
        self.credential_store = CredentialStore()
        self.connection_manager = ConnectionManager(self.credential_store, self)
        self._closing_after_transfer_confirm = False
        self._close_in_progress = False
        self._connect_tasks: dict[str, asyncio.Task] = {}
        self._tab_sessions: dict[str, SessionItem] = {}
        self._tabs_ever_connected: set[str] = set()
        self._background_tasks: set[asyncio.Task] = set()
        self._session_save_timer = QTimer(self)
        self._session_save_timer.setSingleShot(True)
        self._session_save_timer.setInterval(500)
        self._session_save_timer.timeout.connect(self._save_session)
        self._terminal_resize_timer = QTimer(self)
        self._terminal_resize_timer.setSingleShot(True)
        self._terminal_resize_timer.setInterval(80)
        self._terminal_resize_timer.timeout.connect(self._resize_active_terminal)
        self._active_tab_id: Optional[str] = None
        self._sftp_handlers: dict[str, SftpUiHandler] = {}
        self._local_favorites_dialogs: dict[str, LocalFavoritesDialog] = {}
        self._remote_favorites_dialogs: dict[str, RemoteFavoritesDialog] = {}
        self.setWindowTitle(tr('main.window_title'))
        self._main_splitter: Optional[QSplitter] = None
        self._vertical_splitter: Optional[QSplitter] = None
        self._session_tree_width = DEFAULT_SESSION_TREE_WIDTH
        self._title_bar: Optional[WindowTitleBar] = None
        self._shell_frame: Optional[QFrame] = None
        self._init_ui()
        register_retranslator(self.retranslate_ui)
        self._connect_signals()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._restore_session()

    def _init_ui(self) -> None:
        self._shell_frame = QFrame()
        self._shell_frame.setObjectName('MainShellFrame')
        self.setCentralWidget(self._shell_frame)
        root_layout = QVBoxLayout(self._shell_frame)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._title_bar = WindowTitleBar(self)
        root_layout.addWidget(self._title_bar, 0)
        self._setup_menus()

        self.session_panel = SidePanel(
            self.profile_store,
            self.credential_store,
            host_key_store=self.connection_manager.host_keys,
        )
        self.terminal_tabs = TerminalTabWidget()
        self.file_panels = FilePanelsContainer()

        # 上区：Session 树与终端同高
        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.setObjectName('mainSplitter')
        self._main_splitter.addWidget(self.session_panel)
        self._main_splitter.addWidget(self.terminal_tabs)
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setSizes([self._session_tree_width, 920])

        # 下区：双文件 Table 占满全宽
        self._vertical_splitter = QSplitter(Qt.Vertical)
        self._vertical_splitter.addWidget(self._main_splitter)
        self._vertical_splitter.addWidget(self.file_panels)
        self._vertical_splitter.setStretchFactor(0, 3)
        self._vertical_splitter.setStretchFactor(1, 2)
        self._vertical_splitter.setSizes([520, 280])

        root_layout.addWidget(self._vertical_splitter, 1)

    def _setup_menus(self) -> None:
        if self._title_bar is None:
            return
        menubar = self._title_bar.menu_bar
        menubar.clear()

        session_menu = menubar.addMenu(tr('menu.session'))
        self._connect_action = QAction(tr('main.connect'), self)
        self._connect_action.triggered.connect(self._on_connect_clicked)
        session_menu.addAction(self._connect_action)
        session_menu.addSeparator()
        self._exit_action = QAction(tr('menu.exit'), self)
        self._exit_action.setShortcut(QKeySequence.Quit)
        self._exit_action.triggered.connect(self.close)
        session_menu.addAction(self._exit_action)

        settings_menu = menubar.addMenu(tr('menu.settings'))
        self._settings_action = QAction(tr('settings.title'), self)
        self._settings_action.setShortcut('Ctrl+,')
        self._settings_action.triggered.connect(self._on_settings_clicked)
        settings_menu.addAction(self._settings_action)

        help_menu = menubar.addMenu(tr('menu.help'))
        self._about_action = QAction(tr('about.title'), self)
        self._about_action.triggered.connect(self._on_about_clicked)
        help_menu.addAction(self._about_action)

    def _connect_signals(self) -> None:
        self.session_panel.session_connect_requested.connect(self._connect_session)
        self.session_panel.command_send_requested.connect(self._send_command_to_active_terminal)
        self.session_panel.history_jump_requested.connect(self._jump_to_active_terminal_command)
        self.session_panel.sessions_changed.connect(self._schedule_session_save)
        self.terminal_tabs.tab_close_requested.connect(self._on_tab_close_requested)
        self.terminal_tabs.tab_closed.connect(self._on_tab_closed)
        self.terminal_tabs.currentChanged.connect(self._on_current_tab_changed)
        self.connection_manager.remote_list_updated.connect(self._on_remote_list_updated)
        if self._main_splitter is not None:
            self._main_splitter.splitterMoved.connect(self._on_main_splitter_moved)
        if self._vertical_splitter is not None:
            self._vertical_splitter.splitterMoved.connect(self._schedule_session_save)

    def _schedule_session_save(self, *_args) -> None:
        self._session_save_timer.start()

    def _appearance(self):
        return get_app_config().appearance

    def _restore_session(self) -> None:
        window = get_app_config().window
        self._apply_appearance()

        width = window.width if window.width and window.width > 0 else self.DEFAULT_WIDTH
        height = window.height if window.height and window.height > 0 else self.DEFAULT_HEIGHT
        self.resize(width, height)
        self._center_on_screen()

        if window.session_tree_width is not None and self._main_splitter:
            self._session_tree_width = int(window.session_tree_width)
            QTimer.singleShot(0, self._apply_session_tree_width)

        if window.vertical_splitter is not None and self._vertical_splitter:
            QTimer.singleShot(
                0,
                lambda r=float(window.vertical_splitter): self._apply_vertical_splitter_ratio(r),
            )

    def _center_on_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        available = screen.availableGeometry()
        x = available.x() + max(0, (available.width() - self.width()) // 2)
        y = available.y() + max(0, (available.height() - self.height()) // 2)
        self.move(x, y)

    def _apply_session_tree_width(self) -> None:
        if not self._main_splitter:
            return
        total = self._main_splitter.width()
        if total <= 0:
            QTimer.singleShot(50, self._apply_session_tree_width)
            return
        min_terminal = self.MIN_TERMINAL_PANE_WIDTH
        max_tree = max(min_terminal, total - min_terminal)
        tree_width = max(0, min(max_tree, self._session_tree_width))
        self._session_tree_width = tree_width
        self._main_splitter.setSizes([tree_width, max(min_terminal, total - tree_width)])

    def _on_main_splitter_moved(self, _pos: int, _index: int) -> None:
        if not self._main_splitter:
            return
        sizes = self._main_splitter.sizes()
        if sizes:
            self._session_tree_width = sizes[0]
        self._schedule_session_save()

    def _apply_vertical_splitter_ratio(self, ratio: float) -> None:
        if not self._vertical_splitter:
            return
        height = self._vertical_splitter.height()
        if height <= 0:
            QTimer.singleShot(50, lambda: self._apply_vertical_splitter_ratio(ratio))
            return
        self._vertical_splitter.setSizes(splitter_ratio_to_sizes(height, ratio))

    def _save_session(self) -> None:
        try:
            save_window_state(
                width=self.width(),
                height=self.height(),
                session_tree_width=(
                    self._main_splitter.sizes()[0]
                    if self._main_splitter and self._main_splitter.sizes()
                    else self._session_tree_width
                ),
                vertical_splitter=(
                    splitter_sizes_to_ratio(self._vertical_splitter.sizes())
                    if self._vertical_splitter
                    else self.DEFAULT_VERTICAL_SPLITTER_RATIO
                ),
            )
        except OSError as exc:
            logger.warning(f'Failed to save window state: {exc}')

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing_after_transfer_confirm:
            self._save_session()
            super().closeEvent(event)
            return
        if self._close_in_progress:
            event.ignore()
            return
        if self._has_running_transfers():
            if not self._confirm_interrupt_transfers():
                logger.info('Window close cancelled: transfer tasks are still running')
                event.ignore()
                return
            logger.info('Window close confirmed: cancelling running transfer tasks')
            self._cancel_all_transfers()
        event.ignore()
        self._close_in_progress = True
        self._track_background_task(asyncio.create_task(self._finish_close_async()))

    def _track_background_task(self, task: asyncio.Task) -> asyncio.Task:
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
                logger.error(f'Background UI task failed: {error}')

        task.add_done_callback(_done)
        return task

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr('main.window_title'))
        if self._title_bar is not None:
            self._title_bar.set_title(tr('main.window_title'))
        self._setup_menus()
        self.session_panel.retranslate_ui()
        self.terminal_tabs.retranslate_ui()
        self.file_panels.retranslate_ui()

    def _active_files_panel(self) -> Optional[FilesPanel]:
        if not self._active_tab_id:
            return None
        return self.file_panels.get_panel(self._active_tab_id)

    def _register_files_panel(self, tab_id: str, panel: FilesPanel) -> None:
        panel.local_file_panel.path_changed.connect(
            lambda path, tid=tab_id: self._on_local_path_changed_for_tab(tid, path),
        )
        panel.remote_file_panel.path_changed.connect(
            lambda path, tid=tab_id: self._on_remote_path_changed_for_tab(tid, path),
        )
        panel.local_file_panel.set_favorites_provider(
            lambda tid=tab_id: self._local_favorites_for_tab(tid),
        )
        panel.local_file_panel.set_manage_favorites_handler(
            lambda tid=tab_id: self._open_local_favorites_dialog(tid),
        )
        panel.local_file_panel.set_favorites_changed_handler(
            lambda tid=tab_id: self._persist_local_favorites_for_tab(tid),
        )
        panel.remote_file_panel.set_favorites_provider(
            lambda tid=tab_id: self._remote_favorites_for_tab(tid),
        )
        panel.remote_file_panel.set_manage_favorites_handler(
            lambda tid=tab_id: self._open_remote_favorites_dialog(tid),
        )
        panel.remote_file_panel.set_favorites_changed_handler(
            lambda tid=tab_id: self._persist_remote_favorites_for_tab(tid),
        )

    def _session_item_for_tab(self, tab_id: str) -> Optional[SessionItem]:
        session = self._tab_sessions.get(tab_id)
        if session is not None:
            return session
        ssh = self.connection_manager.get_session(tab_id)
        if ssh is None:
            return None
        return ssh.session_item

    def _local_favorites_for_tab(
        self,
        tab_id: str,
    ) -> tuple[Sequence[FavoritePath], Sequence[FavoritePath]]:
        global_entries = list(get_app_config().file_panel.local_favorites)
        session = self._session_item_for_tab(tab_id)
        session_entries = list(session.local_favorites) if session is not None else []
        return global_entries, session_entries

    def _remote_favorites_for_tab(self, tab_id: str) -> Sequence[FavoritePath]:
        session = self._session_item_for_tab(tab_id)
        if session is None:
            return []
        return list(session.remote_favorites)

    def _open_local_favorites_dialog(self, tab_id: str) -> None:
        existing = self._local_favorites_dialogs.get(tab_id)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        panel = self.file_panels.get_panel(tab_id)
        global_entries, session_entries = self._local_favorites_for_tab(tab_id)
        dialog = show_local_favorites_dialog(
            self,
            global_entries=global_entries,
            session_entries=session_entries,
            current_path_provider=(
                panel.local_file_panel.current_path if panel is not None else None
            ),
            on_save=lambda global_list, session_list, tid=tab_id: self._save_local_favorites(
                tid, global_list, session_list,
            ),
        )
        self._local_favorites_dialogs[tab_id] = dialog
        dialog.destroyed.connect(
            lambda _obj=None, tid=tab_id: self._local_favorites_dialogs.pop(tid, None),
        )

    def _open_remote_favorites_dialog(self, tab_id: str) -> None:
        existing = self._remote_favorites_dialogs.get(tab_id)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        panel = self.file_panels.get_panel(tab_id)
        dialog = show_remote_favorites_dialog(
            self,
            session_entries=self._remote_favorites_for_tab(tab_id),
            current_path_provider=(
                panel.remote_file_panel.current_path if panel is not None else None
            ),
            on_save=lambda entries, tid=tab_id: self._save_remote_favorites(tid, entries),
        )
        self._remote_favorites_dialogs[tab_id] = dialog
        dialog.destroyed.connect(
            lambda _obj=None, tid=tab_id: self._remote_favorites_dialogs.pop(tid, None),
        )

    def _save_local_favorites(
        self,
        tab_id: str,
        global_entries: List[FavoritePath],
        session_entries: List[FavoritePath],
    ) -> None:
        try:
            save_file_panel_local_favorites(global_entries)
        except OSError as exc:
            logger.warning(f'Failed to save global local favorites: {exc}')
            message_warning(self, tr('storage.save_failed_title'), tr('storage.save_config_failed'))
        session = self._session_item_for_tab(tab_id)
        if session is not None:
            session.local_favorites = list(session_entries)
            self.session_panel.persist_sessions()

    def _persist_local_favorites_for_tab(self, tab_id: str) -> None:
        panel = self.file_panels.get_panel(tab_id)
        if panel is None:
            return
        global_entries, session_entries = self._local_favorites_for_tab(tab_id)
        self._save_local_favorites(tab_id, list(global_entries), list(session_entries))

    def _save_remote_favorites(self, tab_id: str, entries: List[FavoritePath]) -> None:
        session = self._session_item_for_tab(tab_id)
        if session is None:
            return
        session.remote_favorites = list(entries)
        self.session_panel.persist_sessions()

    def _persist_remote_favorites_for_tab(self, tab_id: str) -> None:
        self._save_remote_favorites(tab_id, list(self._remote_favorites_for_tab(tab_id)))

    def _on_remote_list_updated(self, tab_id: str) -> None:
        if self._active_tab_id != tab_id:
            return
        panel = self.file_panels.get_panel(tab_id)
        if panel is not None:
            panel.remote_file_panel.refresh()

    def _on_local_path_changed_for_tab(self, tab_id: str, path: str) -> None:
        handler = self._sftp_handlers.get(tab_id)
        if handler is not None:
            handler.set_local_dir(path)

    def _on_remote_path_changed_for_tab(self, tab_id: str, path: str) -> None:
        handler = self._sftp_handlers.get(tab_id)
        if handler is not None:
            handler.set_remote_dir(path)

    def _ensure_sftp_handler(self, tab_id: str) -> SftpUiHandler:
        handler = self._sftp_handlers.get(tab_id)
        if handler is None:
            handler = SftpUiHandler(
                tab_id,
                self.connection_manager,
                on_refresh_ui=lambda tid=tab_id: self._refresh_file_panels_for_tab(tid),
                parent=self,
            )
            self._sftp_handlers[tab_id] = handler
        return handler

    def _has_running_transfers(self, tab_id: Optional[str] = None) -> bool:
        if tab_id is not None:
            handler = self._sftp_handlers.get(tab_id)
            return handler.has_running_transfers() if handler is not None else False
        return any(handler.has_running_transfers() for handler in self._sftp_handlers.values())

    def _confirm_interrupt_transfers(self) -> bool:
        return ask_yes_no(
            self,
            tr('file.transfer_running_title'),
            tr('file.transfer_running_body'),
        )

    def _cancel_all_transfers(self) -> None:
        for handler in self._sftp_handlers.values():
            handler.cancel_transfers()

    async def _close_all_async(self) -> None:
        connect_tasks = [task for task in self._connect_tasks.values() if not task.done()]
        for task in connect_tasks:
            task.cancel()
        if connect_tasks:
            await asyncio.gather(*connect_tasks, return_exceptions=True)
        self._connect_tasks.clear()
        current = asyncio.current_task()
        background_tasks = [
            task for task in self._background_tasks
            if task is not current and not task.done()
        ]
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await asyncio.gather(
            *(handler.wait_transfers_closed() for handler in list(self._sftp_handlers.values())),
            return_exceptions=True,
        )
        await self.connection_manager.close_all()

    async def _finish_close_async(self) -> None:
        logger.info('Application close sequence start')
        try:
            self._save_session()
            await self._close_all_async()
            logger.info('Application close sequence done')
            self._closing_after_transfer_confirm = True
            self.close()
        finally:
            self._close_in_progress = False

    def _refresh_file_panels_for_tab(self, tab_id: str) -> None:
        panel = self.file_panels.get_panel(tab_id)
        if panel is None:
            return
        panel.local_file_panel.refresh()
        panel.remote_file_panel.refresh()

    def _refresh_file_panels(self) -> None:
        if self._active_tab_id:
            self._refresh_file_panels_for_tab(self._active_tab_id)

    def _save_active_tab_paths(self) -> None:
        tab_id = self._active_tab_id
        if not tab_id:
            return
        handler = self._sftp_handlers.get(tab_id)
        panel = self.file_panels.get_panel(tab_id)
        if handler is None or panel is None:
            return
        handler.set_local_dir(panel.local_file_panel.current_path())
        handler.set_remote_dir(panel.remote_file_panel.current_path())

    def _attach_file_panel(self, tab_id: str) -> None:
        panel = self.file_panels.get_panel(tab_id)
        if panel is None:
            return
        self.file_panels.show_panel(tab_id)
        handler = self._ensure_sftp_handler(tab_id)
        panel.local_file_panel.set_sftp_handler(handler)
        panel.remote_file_panel.set_sftp_handler(handler)
        callback = self.connection_manager.get_remote_list_callback(tab_id)
        if callback is not None:
            panel.remote_file_panel.set_list_callback(callback)
        else:
            panel.remote_file_panel.clear_remote()

    def _on_connect_clicked(self) -> None:
        session = self.session_panel.current_session()
        if session is not None:
            self._connect_session(session)

    def _send_command_to_active_terminal(self, command: str, execute: bool = True) -> None:
        terminal = self.terminal_tabs.get_current_terminal()
        if terminal is None or not self._terminal_is_alive(terminal):
            return
        terminal.send_command_text(command, execute=execute)
        terminal.setFocus(Qt.OtherFocusReason)

    def _jump_to_active_terminal_command(self, command: str, sent_at: str, command_start_row: int) -> None:
        terminal = self.terminal_tabs.get_current_terminal()
        if terminal is None or not self._terminal_is_alive(terminal):
            return
        terminal.scroll_to_command(command, sent_at, command_start_row)

    def _connect_session(self, session_item: SessionItem) -> None:
        logger.info(
            'Connect session requested: '
            f'session_id={session_item.id}, name={session_item.name}, '
            f'host={session_item.host}, port={session_item.port}, username={session_item.username}'
        )
        self._track_background_task(asyncio.create_task(self._connect_session_async(session_item)))

    async def _connect_session_async(self, session_item: SessionItem) -> None:
        tab_id, terminal = self.terminal_tabs.add_terminal_tab(
            session_item.name,
            host=session_item.host,
        )
        terminal.command_submitted.connect(
            lambda command, sent_at, start_row, tid=tab_id: self.session_panel.add_history_command(
                tid, command, sent_at, start_row
            )
        )
        terminal.reconnect_requested.connect(
            lambda tid=tab_id: self._on_terminal_reconnect_requested(tid)
        )
        self._tab_sessions[tab_id] = session_item
        self._active_tab_id = tab_id
        self.session_panel.set_active_history_tab(tab_id)
        terminal.setFocus(Qt.OtherFocusReason)
        local_path = resolve_local_path(session_item.local_path)
        panel = self.file_panels.create_panel(tab_id, initial_local_path=local_path)
        self.file_panels.show_panel(tab_id)
        self._register_files_panel(tab_id, panel)
        await self._open_session_on_tab(tab_id, session_item, terminal)

    def _on_terminal_reconnect_requested(self, tab_id: str) -> None:
        terminal = self.terminal_tabs.get_terminal(tab_id)
        if terminal is None or not self._terminal_is_alive(terminal):
            return
        if not terminal.is_reconnect_enabled():
            return
        connect_task = self._connect_tasks.get(tab_id)
        if connect_task is not None and not connect_task.done():
            return
        session_item = self._tab_sessions.get(tab_id)
        if session_item is None:
            return
        # Disable immediately so rapid Enter presses cannot spawn concurrent reconnects
        # before _open_session_on_tab registers the connect task.
        terminal.set_reconnect_enabled(False)
        logger.info(
            'Reconnect session requested: '
            f'tab_id={tab_id}, session_id={session_item.id}, host={session_item.host}'
        )
        self._track_background_task(
            asyncio.create_task(self._reconnect_session_async(tab_id))
        )

    async def _reconnect_session_async(self, tab_id: str) -> None:
        session_item = self._tab_sessions.get(tab_id)
        terminal = self.terminal_tabs.get_terminal(tab_id)
        if session_item is None or terminal is None or not self._terminal_is_alive(terminal):
            return
        terminal.set_reconnect_enabled(False)
        await self._open_session_on_tab(tab_id, session_item, terminal)

    def _enable_reconnect_ui(self, terminal: TerminalVTWidget) -> None:
        if not self._terminal_is_alive(terminal) or terminal.is_reconnect_enabled():
            return
        terminal.write_text(tr('terminal.reconnect_hint') + '\r\n')
        terminal.set_reconnect_enabled(True)

    async def _open_session_on_tab(
        self,
        tab_id: str,
        session_item: SessionItem,
        terminal: TerminalVTWidget,
    ) -> None:
        terminal.set_reconnect_enabled(False)
        terminal.write_text(tr('terminal.connecting') + '\r\n')

        current_task = asyncio.current_task()
        if current_task is not None:
            self._connect_tasks[tab_id] = current_task

        def _on_connected() -> None:
            self._tabs_ever_connected.add(tab_id)
            if not self._terminal_is_alive(terminal):
                return
            terminal.set_reconnect_enabled(False)
            terminal.write_text(tr('terminal.connected') + '\r\n')
            self._track_background_task(
                asyncio.create_task(self._init_file_panel_for_session(tab_id, session_item))
            )

        def _on_disconnected() -> None:
            if not self._terminal_is_alive(terminal):
                return
            handler = self._sftp_handlers.get(tab_id)
            if handler is not None:
                handler.cancel_transfers()
            terminal.write_text('\r\n' + tr('terminal.disconnected') + '\r\n')
            # Connect/reconnect failures emit disconnected before the exception
            # handler writes the error line; defer the hint until after that.
            connect_in_flight = (
                current_task is not None
                and self._connect_tasks.get(tab_id) is current_task
                and not current_task.done()
            )
            if tab_id in self._tabs_ever_connected and not connect_in_flight:
                self._enable_reconnect_ui(terminal)
            if self._active_tab_id == tab_id:
                panel = self.file_panels.get_panel(tab_id)
                if panel is not None:
                    panel.remote_file_panel.clear_remote()

        try:
            await self.connection_manager.open_tab(
                tab_id,
                session_item,
                terminal,
                on_connected=_on_connected,
                on_disconnected=_on_disconnected,
                host_key_confirm=self._confirm_host_key,
            )
        except asyncio.CancelledError:
            logger.info(
                'Connect session cancelled: '
                f'session_id={session_item.id}, tab_id={tab_id}'
            )
            return
        except HostKeyChangedError as exc:
            logger.error(
                f'SSH host key changed: host={exc.host}, port={exc.port}, '
                f'expected={exc.expected}, actual={exc.actual}'
            )
            if self._terminal_is_alive(terminal):
                terminal.write_text(tr(
                    'terminal.host_key_changed',
                    host=exc.host,
                    port=exc.port,
                    expected=exc.expected,
                    actual=exc.actual,
                ) + '\r\n')
                if tab_id in self._tabs_ever_connected:
                    self._enable_reconnect_ui(terminal)
        except HostKeyRejectedError:
            if self._terminal_is_alive(terminal):
                terminal.write_text(tr('terminal.host_key_rejected') + '\r\n')
                if tab_id in self._tabs_ever_connected:
                    self._enable_reconnect_ui(terminal)
        except Exception as exc:
            logger.warning(
                'Connect session failed: '
                f'session_id={session_item.id}, name={session_item.name}, '
                f'host={session_item.host}, error={exc}'
            )
            if self._terminal_is_alive(terminal):
                terminal.write_text(tr('terminal.connection_error', error=str(exc)) + '\r\n')
                if tab_id in self._tabs_ever_connected:
                    self._enable_reconnect_ui(terminal)
        finally:
            if self._connect_tasks.get(tab_id) is current_task:
                self._connect_tasks.pop(tab_id, None)

    async def _confirm_host_key(
        self,
        host: str,
        port: int,
        algorithm: str,
        fingerprint: str,
    ) -> bool:
        return await ask_yes_no_async(
            self,
            tr('terminal.host_key_title'),
            tr(
                'terminal.host_key_first_seen',
                host=host,
                port=port,
                algorithm=algorithm,
                fingerprint=fingerprint,
            ),
        )

    @staticmethod
    def _terminal_is_alive(terminal: TerminalVTWidget) -> bool:
        try:
            from PyQt5 import sip
            return not sip.isdeleted(terminal)
        except Exception:
            return True

    async def _init_file_panel_for_session(self, tab_id: str, session_item: SessionItem) -> None:
        panel = self.file_panels.get_panel(tab_id)
        if panel is None:
            return
        if self.connection_manager.get_session(tab_id) is None:
            return
        handler = self._ensure_sftp_handler(tab_id)
        local_path = resolve_local_path(session_item.local_path)
        remote_path = await self.connection_manager.resolve_remote_path(
            tab_id,
            session_item.remote_path,
        )
        if self.file_panels.get_panel(tab_id) is None:
            return
        if (session_item.remote_path or '').strip():
            await self.connection_manager.cd_shell(tab_id, remote_path)
        if handler.try_init_session_paths(local_path, remote_path):
            panel.local_file_panel.set_path(local_path)
            panel.remote_file_panel.set_path(remote_path)
            await self.connection_manager.refresh_remote_list(tab_id, remote_path)
        else:
            # Reconnect: clear_remote() emptied the path bar; restore last remote dir.
            remote_dir = (handler.remote_dir or remote_path or '/').strip() or '/'
            handler.set_remote_dir(remote_dir)
            panel.remote_file_panel.set_path(remote_dir)
            await self.connection_manager.refresh_remote_list(tab_id, remote_dir)
        if self._active_tab_id != tab_id or self.file_panels.get_panel(tab_id) is None:
            return
        self._attach_file_panel(tab_id)
        panel.remote_file_panel.refresh()

    def _on_tab_closed(self, tab_id: str) -> None:
        was_active = self._active_tab_id == tab_id
        self._tab_sessions.pop(tab_id, None)
        self._tabs_ever_connected.discard(tab_id)
        handler = self._sftp_handlers.pop(tab_id, None)
        local_dialog = self._local_favorites_dialogs.pop(tab_id, None)
        if local_dialog is not None:
            local_dialog.close()
        remote_dialog = self._remote_favorites_dialogs.pop(tab_id, None)
        if remote_dialog is not None:
            remote_dialog.close()
        self.file_panels.remove_panel(tab_id)
        self.session_panel.remove_history_tab(tab_id)
        if was_active:
            self._active_tab_id = None
            index = self.terminal_tabs.currentIndex()
            if index >= 0:
                new_tab_id = self.terminal_tabs._tab_ids.get(index)
                if new_tab_id is not None:
                    self._active_tab_id = new_tab_id
                    self.session_panel.set_active_history_tab(new_tab_id)
                    self._attach_file_panel(new_tab_id)
            else:
                self.session_panel.set_active_history_tab(None)
                self.file_panels.show_empty()
        self._track_background_task(
            asyncio.create_task(self._finalize_tab_close_async(tab_id, handler))
        )

    async def _finalize_tab_close_async(
        self,
        tab_id: str,
        handler: Optional[SftpUiHandler],
    ) -> None:
        if handler is not None:
            handler.cancel_transfers()
            try:
                await handler.wait_transfers_closed()
            finally:
                handler.deleteLater()
        connect_task = self._connect_tasks.pop(tab_id, None)
        if connect_task is not None and not connect_task.done():
            connect_task.cancel()
            await asyncio.gather(connect_task, return_exceptions=True)
        await self.connection_manager.close_tab(tab_id)

    def _on_tab_close_requested(self, tab_id: str) -> None:
        if self._has_running_transfers(tab_id):
            if not self._confirm_interrupt_transfers():
                logger.info(f'Tab close cancelled: tab_id={tab_id}, transfer tasks are still running')
                return
            logger.info(f'Tab close confirmed: tab_id={tab_id}, cancelling running transfer tasks')
            handler = self._sftp_handlers.get(tab_id)
            if handler is not None:
                handler.cancel_transfers()
            self._track_background_task(
                asyncio.create_task(self._close_tab_after_transfers_async(tab_id))
            )
            return
        self.terminal_tabs.force_close_tab(tab_id)

    async def _close_tab_after_transfers_async(self, tab_id: str) -> None:
        handler = self._sftp_handlers.get(tab_id)
        if handler is not None:
            await handler.wait_transfers_closed()
        self.terminal_tabs.force_close_tab(tab_id)

    def _on_current_tab_changed(self, index: int) -> None:
        if index < 0:
            self._save_active_tab_paths()
            self.file_panels.show_empty()
            self._active_tab_id = None
            self.session_panel.set_active_history_tab(None)
            return
        self._save_active_tab_paths()
        tab_id = self.terminal_tabs._tab_ids.get(index)
        self._active_tab_id = tab_id
        if tab_id is None:
            self.file_panels.show_empty()
            self.session_panel.set_active_history_tab(None)
            return
        self.session_panel.set_active_history_tab(tab_id)
        panel = self.file_panels.get_panel(tab_id)
        if panel is None:
            self.file_panels.show_empty()
            return
        if self.connection_manager.get_session(tab_id) is not None:
            self._attach_file_panel(tab_id)
        else:
            self.file_panels.show_panel(tab_id)
            panel.remote_file_panel.clear_remote()

    def _current_theme(self) -> str:
        return normalize_theme_name(self._appearance().theme)

    def _apply_appearance(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        appearance = self._appearance()
        family = normalize_terminal_font_family(appearance.terminal_font_family)
        size_px = normalize_terminal_font_size(appearance.terminal_font_size_px)
        apply_app_theme(
            app,
            self._current_theme(),
            size_px,
            family,
        )
        self._apply_terminal_fonts(family, size_px)
        self.session_panel.apply_appearance()
        self.file_panels.apply_file_panel_layout()
        window_cfg = get_app_config().window
        if self._title_bar is not None:
            apply_window_title_bar(
                self._title_bar,
                height=window_cfg.title_bar_height,
                border_width=window_cfg.border_width,
            )
        if self._shell_frame is not None:
            palette = get_theme_palette(self._current_theme())
            apply_main_window_border(
                self._shell_frame,
                palette.window_border,
                window_cfg.border_width,
            )

    def _apply_terminal_fonts(self, family: str, size_px: int) -> None:
        for index in range(self.terminal_tabs.count()):
            widget = self.terminal_tabs.widget(index)
            if isinstance(widget, TerminalVTWidget):
                widget.apply_terminal_font(family, size_px)

    def _save_settings(self, settings: AppSettings) -> None:
        try:
            save_app_preferences(
                theme=settings.theme,
                terminal_font_family=settings.family,
                terminal_font_size_px=settings.size,
                language=settings.language,
            )
        except OSError as exc:
            logger.warning(f'Failed to save app preferences: {exc}')
            message_warning(self, tr('storage.save_failed_title'), tr('storage.save_config_failed'))
            return
        set_language(settings.language)
        self._apply_appearance()

    def _on_settings_clicked(self) -> None:
        appearance = self._appearance()
        prompt_app_settings(
            self,
            self._current_theme(),
            appearance.terminal_font_size_px,
            appearance.terminal_font_family,
            get_app_config().language,
            on_save=self._save_settings,
        )

    def _on_about_clicked(self) -> None:
        show_about_dialog(self)

    def _resize_margin(self) -> int:
        border = get_app_config().window.border_width
        return max(self.RESIZE_MARGIN, border + 2)

    def _resize_edge_at(self, pos: QPoint) -> Qt.Edges:
        if self.isMaximized():
            return Qt.Edges()
        margin = self._resize_margin()
        rect = self.rect()
        edges = Qt.Edges()
        if pos.x() <= margin:
            edges |= Qt.LeftEdge
        if pos.x() >= rect.width() - margin:
            edges |= Qt.RightEdge
        if pos.y() <= margin:
            edges |= Qt.TopEdge
        if pos.y() >= rect.height() - margin:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _splitter_handle_at(widget: Optional[QWidget]) -> Optional[QSplitterHandle]:
        while widget is not None:
            if isinstance(widget, QSplitterHandle):
                return widget
            widget = widget.parentWidget()
        return None

    @staticmethod
    def _cursor_for_splitter_handle(handle: QSplitterHandle) -> Qt.CursorShape:
        return Qt.SizeHorCursor if handle.orientation() == Qt.Horizontal else Qt.SizeVerCursor

    @staticmethod
    def _cursor_for_edges(edges: Qt.Edges) -> Qt.CursorShape:
        if not edges:
            return Qt.ArrowCursor
        has_left = bool(edges & Qt.LeftEdge)
        has_right = bool(edges & Qt.RightEdge)
        has_top = bool(edges & Qt.TopEdge)
        has_bottom = bool(edges & Qt.BottomEdge)
        if (has_left and has_top) or (has_right and has_bottom):
            return Qt.SizeFDiagCursor
        if (has_right and has_top) or (has_left and has_bottom):
            return Qt.SizeBDiagCursor
        if has_left or has_right:
            return Qt.SizeHorCursor
        if has_top or has_bottom:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def eventFilter(self, watched, event) -> bool:
        if not isinstance(watched, QWidget) or watched.window() is not self:
            return super().eventFilter(watched, event)
        if not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)

        local_pos = self.mapFromGlobal(event.globalPos())
        edges = self._resize_edge_at(local_pos)

        if event.type() == QEvent.MouseMove and event.buttons() == Qt.NoButton and not self.isMaximized():
            hover = QApplication.widgetAt(event.globalPos())
            handle = self._splitter_handle_at(hover)
            if handle is not None:
                handle.setCursor(self._cursor_for_splitter_handle(handle))
                return False
            watched.setCursor(self._cursor_for_edges(edges))
            return False

        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_session_tree_width()
        if self._active_tab_id is not None:
            self._terminal_resize_timer.start()

    def _resize_active_terminal(self) -> None:
        if self._active_tab_id is not None:
            self._track_background_task(
                asyncio.create_task(self.connection_manager.resize_terminal(self._active_tab_id))
            )
