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


from ui.file_panel.tables import (
    _BaseFileTable,
    _is_local_root,
    _list_windows_drives,
    _windows_drive_root,
)
from ui.file_panel.helpers import _format_size

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


class _FilePanelStatusBar(QWidget):
    """Bottom status bar with incremental file filter and transfer hint."""

    def __init__(self, table: _BaseFileTable, *, transfer_kind: str, parent: QWidget = None) -> None:
        super().__init__(parent)
        self._table = table
        self._transfer_kind = transfer_kind
        self._transfer_active = False
        self._transfer_progress = 0.0
        self.setObjectName('filePanelStatusBar')
        self._progress_bar = _FileTransferProgressBar(self, transfer_kind=transfer_kind)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.file_filter_edit = QLineEdit()
        self.file_filter_edit.setObjectName('fileFilterEdit')
        self.file_filter_edit.hide()
        layout.addWidget(self.file_filter_edit)

        self._summary_label = QLabel()
        self._summary_label.setObjectName('filePanelStatusText')
        self._summary_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._summary_label, 1)

        self._speed_label = QLabel()
        self._speed_label.setObjectName('filePanelTransferSpeed')
        self._speed_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._speed_label.hide()
        layout.addWidget(self._speed_label)

        self._transfer_button = QToolButton()
        self._transfer_button.setObjectName('filePanelStatusButton')
        self._transfer_button.setAutoRaise(True)
        self._transfer_button.setEnabled(False)
        self._transfer_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._transfer_button.setText('↑' if transfer_kind == 'upload' else '↓')
        self._transfer_button.hide()
        layout.addWidget(self._transfer_button)

        self._property_button = QToolButton()
        self._property_button.setObjectName('filePanelStatusButton')
        self._property_button.setAutoRaise(True)
        self._property_button.setEnabled(False)
        self._property_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._property_button.setText('⚙')
        self._property_button.hide()
        layout.addWidget(self._property_button)

        table.status_counts_changed.connect(self._update_counts)
        table.filter_text_changed.connect(self._sync_filter_text)
        table.filter_focus_requested.connect(self.focus_filter)
        table.filter_cancelled.connect(self.hide_filter)
        table.property_status_changed.connect(self.set_property_status)
        self.file_filter_edit.textChanged.connect(table.set_filter_text)
        self.file_filter_edit.installEventFilter(self)
        table.status_counts_changed.emit(0, 0, 0, 0)
        self.apply_layout()

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self.file_filter_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self._table.clear_filter()
                self._table.setFocus(Qt.OtherFocusReason)
                return True
        if obj is self.file_filter_edit and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            self._table.set_filter_edit_focused(event.type() == QEvent.FocusIn)
        return super().eventFilter(obj, event)

    def _sync_filter_text(self, text: str) -> None:
        if self.file_filter_edit.text() != text:
            self.file_filter_edit.blockSignals(True)
            self.file_filter_edit.setText(text)
            self.file_filter_edit.blockSignals(False)
        if text:
            self.focus_filter()

    def focus_filter(self) -> None:
        self.file_filter_edit.show()
        self.file_filter_edit.setFocus(Qt.OtherFocusReason)
        text = self.file_filter_edit.text()
        if text:
            self.file_filter_edit.setCursorPosition(len(text))
        QTimer.singleShot(0, self._update_progress_bar_geometry)

    def hide_filter(self) -> None:
        self.file_filter_edit.hide()
        self._update_progress_bar_geometry()

    def _update_counts(
        self,
        selected_files: int,
        total_files: int,
        selected_dirs: int,
        total_dirs: int,
    ) -> None:
        self._summary_label.setText(
            tr(
                'file.status_counts',
                selected_files=selected_files,
                total_files=total_files,
                selected_dirs=selected_dirs,
                total_dirs=total_dirs,
            ),
        )

    def set_transfer_status(
        self,
        kind: str,
        speed_text: str,
        active: bool,
        progress: float,
        transferred_bytes: int,
        total_bytes: int,
    ) -> None:
        if kind != self._transfer_kind:
            return
        self._transfer_active = active
        self._transfer_progress = max(0.0, min(1.0, float(progress)))
        percent = f'{self._transfer_progress * 100:.0f}%'
        self._speed_label.setText(f'{speed_text}  {percent}' if speed_text else percent)
        self._speed_label.setToolTip(
            tr(
                'file.transfer_tooltip',
                transferred=_format_size(max(0, int(transferred_bytes))),
                total=_format_size(max(0, int(total_bytes))),
            ) if active else ''
        )
        self._speed_label.setVisible(active)
        self._transfer_button.setVisible(active)
        self._update_progress_bar_geometry()
        self._progress_bar.set_progress(self._transfer_progress if active else 0.0, active)

    def set_property_status(self, active: bool, done: int, total: int, failed: int) -> None:
        if not active:
            self._property_button.hide()
            self._property_button.setToolTip('')
            return
        if total > 0:
            tooltip = tr('file.properties.progress', done=done, total=total)
        else:
            tooltip = tr('file.properties.progress_unknown', done=done)
        if failed:
            tooltip = f"{tooltip}\n{tr('file.properties.failed', count=failed)}"
        self._property_button.setToolTip(tooltip)
        self._property_button.show()

    def apply_layout(self) -> None:
        cfg = get_app_config().file_panel
        font = QFont()
        font.setPixelSize(cfg.file_panel_statusbar_font_size)
        for widget in (
            self.file_filter_edit,
            self._summary_label,
            self._speed_label,
            self._transfer_button,
            self._property_button,
        ):
            widget.setFont(font)
        height = max(20, cfg.file_panel_statusbar_font_size + 8)
        self.setFixedHeight(height)
        self.file_filter_edit.setFixedHeight(height)
        square = QSize(height, height)
        self._transfer_button.setFixedSize(square)
        self._property_button.setFixedSize(square)
        self._update_progress_bar_geometry()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_progress_bar_geometry()

    def _update_progress_bar_geometry(self) -> None:
        x = 0
        if not self.file_filter_edit.isHidden():
            x = self.file_filter_edit.geometry().right() + 1
        self._progress_bar.setGeometry(
            x,
            max(0, self.height() - 4),
            max(0, self.width() - x),
            4,
        )


class _FileTransferProgressBar(QWidget):
    def __init__(self, parent: QWidget, *, transfer_kind: str) -> None:
        super().__init__(parent)
        self._transfer_kind = transfer_kind
        self._active = False
        self._progress = 0.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_progress(self, progress: float, active: bool) -> None:
        self._active = active
        self._progress = max(0.0, min(1.0, float(progress)))
        self.setVisible(active)
        if active:
            self.raise_()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._active:
            return
        palette = active_theme_palette()
        painter = QPainter(self)
        try:
            track = QColor(palette.background_secondary)
            fill = QColor(palette.highlight if self._transfer_kind == 'upload' else palette.status_success)
            painter.fillRect(QRect(0, 0, self.width(), self.height()), track)
            progress_width = int(self.width() * self._progress)
            if progress_width > 0:
                painter.fillRect(QRect(0, 0, progress_width, self.height()), fill)
        finally:
            painter.end()


def _show_favorites_menu(
    parent: QWidget,
    anchor: QWidget,
    *,
    sections: Sequence[tuple[str, Sequence[FavoritePath]]],
    on_manage: Optional[Callable[[], None]],
    on_navigate: Optional[Callable[[FavoritePath], None]],
) -> None:
    menu = _FavoriteMenu(parent, on_navigate=on_navigate)
    menu_font = QFont()
    menu_font.setPixelSize(get_app_config().file_panel.file_panel_favorites_menu_font_size)
    menu.setFont(menu_font)
    manage_action = menu.addAction(tr('file.favorites.manage'))
    add_menu_key(menu, manage_action, Qt.Key_M)
    path_actions: list[tuple[QAction, FavoritePath]] = []
    for title, entries in sections:
        if not entries:
            continue
        menu.addSeparator()
        if title:
            header = menu.addAction(title)
            header.setEnabled(False)
        for entry in entries:
            shortcut_index = len(path_actions)
            prefix = _favorite_menu_shortcut_prefix(shortcut_index)
            action = menu.addAction(f'{prefix}{entry.display_text()}')
            path_actions.append((action, entry))
            menu.register_path_shortcut(shortcut_index, entry)
    chosen = exec_menu(menu, anchor.mapToGlobal(anchor.rect().bottomLeft()))
    if chosen is None:
        return
    if chosen == manage_action:
        if on_manage is not None:
            on_manage()
        return
    for action, entry in path_actions:
        if chosen == action and on_navigate is not None:
            on_navigate(entry)
            return


def _favorite_menu_shortcut_prefix(index: int) -> str:
    keys = ('1', '2', '3', '4', '5', '6', '7', '8', '9', '0')
    if 0 <= index < len(keys):
        return f'{keys[index]}  '
    return ''


class _FavoriteMenu(ShortcutMenu):
    def __init__(
        self,
        parent: QWidget = None,
        *,
        on_navigate: Optional[Callable[[FavoritePath], None]],
    ) -> None:
        super().__init__(parent)
        self._on_navigate = on_navigate
        self._shortcut_paths: dict[int, FavoritePath] = {}

    def register_path_shortcut(self, index: int, entry: FavoritePath) -> None:
        if 0 <= index < 10:
            key = Qt.Key_0 if index == 9 else Qt.Key_1 + index
            self._shortcut_paths[key] = entry

    def keyPressEvent(self, event: QKeyEvent) -> None:
        entry = self._shortcut_paths.get(event.key())
        if entry is not None:
            if self._on_navigate is not None:
                self._on_navigate(entry)
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)
