#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import Optional

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
from i18n import register_retranslator, set_language, tr
from models.session_item import SessionItem
from storage.app_config import get_app_config, save_app_preferences, save_window_state
from storage.keyring_store import KeyringStore
from storage.session_profile_store import SessionProfileStore
from ui.about_dialog import show_about_dialog
from ui.settings_dialog import AppSettings, prompt_app_settings
from ui.file_table_panel import FilePanelsContainer, FilesPanel
from ui.session_tree_panel import SessionTreePanel
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
    DEFAULT_MAIN_SPLITTER_RATIO = round(280 / (280 + 920), 3)
    DEFAULT_VERTICAL_SPLITTER_RATIO = 0.65
    RESIZE_MARGIN = 5

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.profile_store = SessionProfileStore()
        self.keyring_store = KeyringStore()
        self.connection_manager = ConnectionManager(self.keyring_store, self)
        self._session_save_timer = QTimer(self)
        self._session_save_timer.setSingleShot(True)
        self._session_save_timer.setInterval(500)
        self._session_save_timer.timeout.connect(self._save_session)
        self._active_tab_id: Optional[str] = None
        self._sftp_handlers: dict[str, SftpUiHandler] = {}
        self.setWindowTitle(tr('main.window_title'))
        self._main_splitter: Optional[QSplitter] = None
        self._vertical_splitter: Optional[QSplitter] = None
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

        self.session_panel = SessionTreePanel(self.profile_store, self.keyring_store)
        self.terminal_tabs = TerminalTabWidget()
        self.file_panels = FilePanelsContainer()

        # 上区：Session 树与终端同高
        self._main_splitter = QSplitter(Qt.Horizontal)
        self._main_splitter.setObjectName('mainSplitter')
        self._main_splitter.addWidget(self.session_panel)
        self._main_splitter.addWidget(self.terminal_tabs)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 3)
        self._main_splitter.setSizes([280, 920])

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
        self.session_panel.sessions_changed.connect(self._schedule_session_save)
        self.terminal_tabs.tab_closed.connect(self._on_tab_closed)
        self.terminal_tabs.currentChanged.connect(self._on_current_tab_changed)
        self.connection_manager.remote_list_updated.connect(self._on_remote_list_updated)
        if self._main_splitter is not None:
            self._main_splitter.splitterMoved.connect(self._schedule_session_save)
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

        if window.main_splitter is not None and self._main_splitter:
            QTimer.singleShot(0, lambda r=float(window.main_splitter): self._apply_main_splitter_ratio(r))

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

    def _apply_main_splitter_ratio(self, ratio: float) -> None:
        if not self._main_splitter:
            return
        width = self._main_splitter.width()
        if width <= 0:
            QTimer.singleShot(50, lambda: self._apply_main_splitter_ratio(ratio))
            return
        self._main_splitter.setSizes(splitter_ratio_to_sizes(width, ratio))

    def _apply_vertical_splitter_ratio(self, ratio: float) -> None:
        if not self._vertical_splitter:
            return
        height = self._vertical_splitter.height()
        if height <= 0:
            QTimer.singleShot(50, lambda: self._apply_vertical_splitter_ratio(ratio))
            return
        self._vertical_splitter.setSizes(splitter_ratio_to_sizes(height, ratio))

    def _save_session(self) -> None:
        save_window_state(
            width=self.width(),
            height=self.height(),
            main_splitter=(
                splitter_sizes_to_ratio(self._main_splitter.sizes())
                if self._main_splitter
                else self.DEFAULT_MAIN_SPLITTER_RATIO
            ),
            vertical_splitter=(
                splitter_sizes_to_ratio(self._vertical_splitter.sizes())
                if self._vertical_splitter
                else self.DEFAULT_VERTICAL_SPLITTER_RATIO
            ),
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_session()
        asyncio.create_task(self.connection_manager.close_all())
        super().closeEvent(event)

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
                on_refresh_ui=self._refresh_file_panels,
                parent=self,
            )
            self._sftp_handlers[tab_id] = handler
        return handler

    def _refresh_file_panels(self) -> None:
        panel = self._active_files_panel()
        if panel is None:
            return
        panel.local_file_panel.refresh()
        panel.remote_file_panel.refresh()

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
        panel.remote_file_panel.set_sftp_handler(handler)
        callback = self.connection_manager.get_remote_list_callback(tab_id)
        if callback is not None:
            panel.remote_file_panel.set_list_callback(callback)
        else:
            panel.remote_file_panel.clear_remote()

    def _on_connect_clicked(self) -> None:
        item = self.session_panel.tree.currentItem()
        if item is None:
            return
        session = self.session_panel._find_session_by_tree(item)
        if session is not None and not session.is_folder():
            self._connect_session(session)

    def _connect_session(self, session_item: SessionItem) -> None:
        asyncio.create_task(self._connect_session_async(session_item))

    async def _connect_session_async(self, session_item: SessionItem) -> None:
        tab_id, terminal = self.terminal_tabs.add_terminal_tab(session_item.name)
        self._active_tab_id = tab_id
        panel = self.file_panels.create_panel(tab_id)
        self.file_panels.show_panel(tab_id)
        self._register_files_panel(tab_id, panel)
        terminal.write_text(tr('terminal.connecting') + '\r\n')

        def _on_connected() -> None:
            if not self._terminal_is_alive(terminal):
                return
            terminal.write_text(tr('terminal.connected') + '\r\n')
            asyncio.create_task(self._init_file_panel_for_session(tab_id, session_item))

        def _on_disconnected() -> None:
            if not self._terminal_is_alive(terminal):
                return
            terminal.write_text('\r\n' + tr('terminal.disconnected') + '\r\n')
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
            )
        except Exception as exc:
            if self._terminal_is_alive(terminal):
                terminal.write_text(tr('terminal.connection_error', error=str(exc)) + '\r\n')

    @staticmethod
    def _terminal_is_alive(terminal: TerminalVTWidget) -> bool:
        try:
            from PyQt5 import sip
            return not sip.isdeleted(terminal)
        except Exception:
            return True

    async def _init_file_panel_for_session(self, tab_id: str, session_item: SessionItem) -> None:
        handler = self._ensure_sftp_handler(tab_id)
        panel = self.file_panels.get_panel(tab_id)
        if panel is None:
            return
        local_path = resolve_local_path(session_item.local_path)
        remote_path = await self.connection_manager.resolve_remote_path(
            tab_id,
            session_item.remote_path,
        )
        if (session_item.remote_path or '').strip():
            await self.connection_manager.cd_shell(tab_id, remote_path)
        if handler.try_init_session_paths(local_path, remote_path):
            panel.local_file_panel.set_path(local_path)
            panel.remote_file_panel.set_path(remote_path)
            await self.connection_manager.refresh_remote_list(tab_id, remote_path)
        else:
            await self.connection_manager.refresh_remote_list(tab_id, handler.remote_dir)
        if self._active_tab_id != tab_id:
            return
        self._attach_file_panel(tab_id)
        panel.remote_file_panel.refresh()

    def _on_tab_closed(self, tab_id: str) -> None:
        asyncio.create_task(self.connection_manager.close_tab(tab_id))
        self._sftp_handlers.pop(tab_id, None)
        self.file_panels.remove_panel(tab_id)
        if self._active_tab_id == tab_id:
            self._active_tab_id = None
            index = self.terminal_tabs.currentIndex()
            if index >= 0:
                new_tab_id = self.terminal_tabs._tab_ids.get(index)
                if new_tab_id is not None:
                    self._active_tab_id = new_tab_id
                    self._attach_file_panel(new_tab_id)
            else:
                self.file_panels.show_empty()

    def _on_current_tab_changed(self, index: int) -> None:
        if index < 0:
            self._save_active_tab_paths()
            self.file_panels.show_empty()
            self._active_tab_id = None
            return
        self._save_active_tab_paths()
        tab_id = self.terminal_tabs._tab_ids.get(index)
        self._active_tab_id = tab_id
        if tab_id is None:
            self.file_panels.show_empty()
            return
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
        save_app_preferences(
            theme=settings.theme,
            terminal_font_family=settings.family,
            terminal_font_size_px=settings.size,
            language=settings.language,
        )
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
        if self._active_tab_id is not None:
            asyncio.create_task(self.connection_manager.resize_terminal(self._active_tab_id))
