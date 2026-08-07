#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Left side drawer panel for sessions, quick commands, and command history."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QKeyEvent
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QToolButton,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from models.command_history_item import CommandHistoryItem
from models.command_item import CommandItem
from models.session_item import AUTH_PASSWORD, AUTH_PUBLIC_KEY, SessionItem
from storage.app_config import get_app_config
from storage.command_history_store import CommandHistoryStore
from storage.command_store import CommandStore
from storage.credential_store import CredentialStore
from storage.host_key_store import HostKeyStore
from storage.session_profile_store import SessionProfileStore
from ui.dialog_common import add_form_field, create_form_grid
from ui.dialog_i18n import ask_yes_no, message_warning, translate_button_box
from ui.menu_shortcuts import ShortcutMenu, add_menu_key, exec_menu
from ui.prompt_dialog import prompt_text
from ui.favorite_tree_widget import (
    FavoriteTreeWidget,
    ITEM_TYPE_FOLDER,
    ITEM_TYPE_SESSION,
    ROLE_ITEM_ID,
    ROLE_TYPE,
)
from ui.session_dialog import SessionDialog


DRAWER_SESSIONS = 'sessions'
DRAWER_COMMANDS = 'commands'
DRAWER_HISTORY = 'history'
ITEM_TYPE_COMMAND = 'command'


def _session_tooltip(session: SessionItem) -> str:
    """Build a multi-line tooltip with connection details (never includes password)."""
    if session.is_folder():
        return ''
    auth = (
        tr('sessions.auth_publickey')
        if session.auth_type == AUTH_PUBLIC_KEY
        else tr('sessions.auth_password')
    )
    lines = [
        f"{tr('sessions.name')}: {session.name}",
        f"{tr('sessions.host')}: {session.host}",
        f"{tr('sessions.port')}: {session.port}",
        f"{tr('sessions.username')}: {session.username}",
        f"{tr('sessions.auth_type')}: {auth}",
    ]
    if session.auth_type == AUTH_PUBLIC_KEY and session.key_path:
        lines.append(f"{tr('sessions.key_path')}: {session.key_path}")
    if session.local_path:
        lines.append(f"{tr('sessions.local_path')}: {session.local_path}")
    if session.remote_path:
        lines.append(f"{tr('sessions.remote_path')}: {session.remote_path}")
    return '\n'.join(lines)


def _command_tooltip(command: CommandItem) -> str:
    if command.is_folder():
        return ''
    lines = [
        f"{tr('commands.name')}: {command.name}",
        f"{tr('commands.command')}: {command.command}",
    ]
    if command.description:
        lines.append(f"{tr('commands.description')}: {command.description}")
    return '\n'.join(lines)


def _history_tooltip(item: CommandHistoryItem) -> str:
    return '\n'.join([
        f"{tr('history.sent_at')}: {item.sent_at}",
        item.command,
    ])


class CommandDialog(QDialog):
    """Create or edit a quick command."""

    def __init__(
        self,
        parent: QWidget,
        *,
        command: Optional[CommandItem] = None,
        title: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        grid = create_form_grid()

        self.name_edit = QLineEdit(command.name if command is not None else '')
        self.command_edit = QTextEdit(command.command if command is not None else '')
        self.command_edit.setAcceptRichText(False)
        self.command_edit.setMinimumHeight(90)
        self.description_edit = QTextEdit(command.description if command is not None else '')
        self.description_edit.setAcceptRichText(False)
        self.description_edit.setMinimumHeight(70)

        add_form_field(grid, 0, tr('commands.name'), self.name_edit)
        add_form_field(grid, 1, tr('commands.command'), self.command_edit)
        add_form_field(grid, 2, tr('commands.description'), self.description_edit)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        translate_button_box(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.setFocus()

    def accept(self) -> None:  # type: ignore[override]
        if not self.name_edit.text().strip():
            message_warning(self, tr('commands.validation_title'), tr('commands.validation_name_required'))
            self.name_edit.setFocus()
            return
        if not self.command_edit.toPlainText().strip():
            message_warning(self, tr('commands.validation_title'), tr('commands.validation_command_required'))
            self.command_edit.setFocus()
            return
        super().accept()

    def get_command(self, existing: Optional[CommandItem] = None) -> Optional[CommandItem]:
        name = self.name_edit.text().strip()
        command = self.command_edit.toPlainText().strip()
        if not name or not command:
            return None
        item = existing or CommandItem()
        item.name = name
        item.command = command
        item.description = self.description_edit.toPlainText().strip()
        item.children = []
        return item


class SidePanel(QWidget):
    """Left drawer panel for sessions, quick commands, and command history."""

    session_connect_requested = pyqtSignal(SessionItem)
    command_send_requested = pyqtSignal(str)
    history_jump_requested = pyqtSignal(str, str, int)
    sessions_changed = pyqtSignal()

    def __init__(
        self,
        session_store: SessionProfileStore,
        credential_store: Optional[CredentialStore] = None,
        command_store: Optional[CommandStore] = None,
        history_store: Optional[CommandHistoryStore] = None,
        host_key_store: Optional[HostKeyStore] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.store = session_store
        self.keyring = credential_store or CredentialStore()
        self.command_store = command_store or CommandStore()
        self.history_store = history_store or CommandHistoryStore()
        self.host_keys = host_key_store or HostKeyStore()
        self._items: List[SessionItem] = []
        self._commands: List[CommandItem] = []
        self._history_items: List[CommandHistoryItem] = []
        self._active_drawer = DRAWER_SESSIONS
        self._active_history_tab_id: Optional[str] = None
        self._history_scroll_positions: Dict[str, int] = {}
        self._filter_texts: Dict[str, str] = {
            DRAWER_SESSIONS: '',
            DRAWER_COMMANDS: '',
            DRAWER_HISTORY: '',
        }
        self._init_ui()
        self.reload()
        self.reload_commands()
        self.reload_history()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._drawer_buttons: Dict[str, QToolButton] = {}
        self._drawer_symbols: Dict[str, QLabel] = {}
        self._drawer_titles: Dict[str, QLabel] = {}

        self._sessions_button = self._create_drawer_button(DRAWER_SESSIONS)
        layout.addWidget(self._sessions_button)

        self.tree = FavoriteTreeWidget()
        self.tree.setObjectName('SessionTree')
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemMoved.connect(self._on_tree_item_moved)
        self.tree.renameRequested.connect(self._rename_item)
        self.tree.deleteRequested.connect(lambda item: self._delete_item(item, confirm=True))
        self.tree.setAnimated(True)
        self.tree.setIndentation(16)
        layout.addWidget(self.tree, 1)

        self._commands_button = self._create_drawer_button(DRAWER_COMMANDS)
        layout.addWidget(self._commands_button)

        self.commands_tree = FavoriteTreeWidget()
        self.commands_tree.empty_hint_key = 'commands.empty_hint'
        self.commands_tree.setObjectName('CommandTree')
        self.commands_tree.setHeaderHidden(True)
        self.commands_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.commands_tree.customContextMenuRequested.connect(self._show_command_context_menu)
        self.commands_tree.itemDoubleClicked.connect(self._on_command_item_double_clicked)
        self.commands_tree.itemMoved.connect(self._on_command_tree_item_moved)
        self.commands_tree.renameRequested.connect(self._rename_command_item)
        self.commands_tree.deleteRequested.connect(lambda item: self._delete_command_item(item, confirm=True))
        self.commands_tree.setAnimated(True)
        self.commands_tree.setIndentation(16)
        layout.addWidget(self.commands_tree, 1)

        self._history_button = self._create_drawer_button(DRAWER_HISTORY)
        layout.addWidget(self._history_button)

        self.history_list = QListWidget()
        self.history_list.setObjectName('CommandHistoryList')
        self.history_list.itemClicked.connect(self._on_history_item_clicked)
        self.history_list.itemDoubleClicked.connect(self._on_history_item_double_clicked)
        layout.addWidget(self.history_list, 1)

        self._filter_edit = QLineEdit()
        self._filter_edit.setObjectName('SessionFilterEdit')
        self._filter_edit.setClearButtonEnabled(False)
        self._filter_edit.textChanged.connect(self._on_filter_text_changed)
        layout.addWidget(self._filter_edit)
        self.apply_appearance()
        self._set_active_drawer(DRAWER_SESSIONS)

        self.tree.installEventFilter(self)
        self.tree.viewport().installEventFilter(self)
        self.commands_tree.installEventFilter(self)
        self.commands_tree.viewport().installEventFilter(self)
        self.history_list.installEventFilter(self)
        self.history_list.viewport().installEventFilter(self)
        self._filter_edit.installEventFilter(self)

    def _create_drawer_button(self, drawer: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName('drawerHeaderButton')
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, d=drawer: self._set_active_drawer(d))
        header_layout = QHBoxLayout(button)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        symbol = QLabel(button)
        symbol.setObjectName('drawerHeaderSymbol')
        symbol.setAlignment(Qt.AlignCenter)
        symbol.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        title = QLabel(button)
        title.setObjectName('drawerHeaderTitle')
        title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(symbol)
        header_layout.addWidget(title, 1)
        self._drawer_buttons[drawer] = button
        self._drawer_symbols[drawer] = symbol
        self._drawer_titles[drawer] = title
        return button

    def _set_active_drawer(self, drawer: str) -> None:
        self._filter_texts[self._active_drawer] = self._filter_edit.text()
        self._active_drawer = drawer
        self.tree.setVisible(drawer == DRAWER_SESSIONS)
        self.commands_tree.setVisible(drawer == DRAWER_COMMANDS)
        self.history_list.setVisible(drawer == DRAWER_HISTORY)
        for key, button in self._drawer_buttons.items():
            button.setChecked(key == drawer)
        self._update_drawer_texts()
        self._filter_edit.setPlaceholderText(tr(self._filter_placeholder_key()))
        self._filter_edit.setText(self._filter_texts.get(drawer, ''))
        self._apply_active_filter()

    def _drawer_title_key(self, drawer: str) -> str:
        return {
            DRAWER_SESSIONS: 'sessions.title',
            DRAWER_COMMANDS: 'commands.title',
            DRAWER_HISTORY: 'history.title',
        }[drawer]

    def _filter_placeholder_key(self) -> str:
        return {
            DRAWER_SESSIONS: 'sessions.filter_placeholder',
            DRAWER_COMMANDS: 'commands.filter_placeholder',
            DRAWER_HISTORY: 'history.filter_placeholder',
        }[self._active_drawer]

    def _update_drawer_texts(self) -> None:
        for drawer in self._drawer_buttons:
            self._drawer_symbols[drawer].setText('-' if drawer == self._active_drawer else '+')
            self._drawer_titles[drawer].setText(tr(self._drawer_title_key(drawer)))

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.KeyPress and isinstance(event, QKeyEvent):
            if (
                obj in (self.tree, self.commands_tree, self.history_list, self._filter_edit)
                and event.key() == Qt.Key_Escape
                and self._filter_edit.text().strip()
            ):
                self._clear_filter()
                return True

            filter_sources = (
                self.tree,
                self.tree.viewport(),
                self.commands_tree,
                self.commands_tree.viewport(),
                self.history_list,
                self.history_list.viewport(),
            )
            if obj in filter_sources and self._begin_filter_from_tree(event):
                return True

        return super().eventFilter(obj, event)

    def _begin_filter_from_tree(self, event: QKeyEvent) -> bool:
        """Move focus to filter and seed it with the typed printable character."""
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False

        text = event.text()
        if not text or not text.isprintable() or text.isspace():
            return False

        self._filter_edit.setText(text)
        self._filter_edit.setFocus(Qt.OtherFocusReason)
        self._filter_edit.setCursorPosition(len(text))
        return True

    def reload(self) -> None:
        self._items = self.store.load()
        self._rebuild_tree()

    def reload_commands(self) -> None:
        self._commands = self.command_store.load()
        self._rebuild_command_tree()

    def reload_history(self) -> None:
        self._history_items = self.history_store.load_for_tab(self._active_history_tab_id)
        self._rebuild_history_list()

    def set_active_history_tab(self, tab_id: Optional[str]) -> None:
        self._save_history_scroll_position()
        self._active_history_tab_id = tab_id
        self.reload_history()

    def add_history_command(self, tab_id: str, command: str, sent_at: str, command_start_row: int) -> None:
        items = self.history_store.add(tab_id, command, sent_at, command_start_row)
        if tab_id == self._active_history_tab_id:
            self._save_history_scroll_position()
            self._history_items = items
            self._rebuild_history_list()

    def remove_history_tab(self, tab_id: str) -> None:
        self._history_scroll_positions.pop(tab_id, None)
        self.history_store.remove_tab(tab_id)
        if tab_id == self._active_history_tab_id:
            self._history_items = []
            self._rebuild_history_list()

    def persist_sessions(self) -> bool:
        """Write current in-memory session tree (including favorites) to disk."""
        return self._save_sessions()

    def _save_sessions(self) -> bool:
        if self.store.save_items(self._items):
            return True
        message_warning(self, tr('storage.save_failed_title'), tr('storage.save_sessions_failed'))
        return False

    def _save_commands(self) -> bool:
        if self.command_store.save_items(self._commands):
            return True
        message_warning(self, tr('storage.save_failed_title'), tr('storage.save_commands_failed'))
        return False

    def _save_credential_result(self, saved: bool) -> bool:
        if saved:
            return True
        message_warning(self, tr('storage.save_failed_title'), tr('storage.save_credentials_failed'))
        return False

    def _rebuild_tree(self) -> None:
        self.tree.clear()
        for item in self._items:
            self.tree.addTopLevelItem(self._session_to_tree(item))
        self.tree.expandAll()
        self._filter_session_items(self._filter_texts.get(DRAWER_SESSIONS, '').strip().lower())
        self.tree.viewport().update()

    def _rebuild_command_tree(self) -> None:
        self.commands_tree.clear()
        for item in self._commands:
            self.commands_tree.addTopLevelItem(self._command_to_tree(item))
        self.commands_tree.expandAll()
        self._filter_command_items(self._filter_texts.get(DRAWER_COMMANDS, '').strip().lower())
        self.commands_tree.viewport().update()

    def _rebuild_history_list(self) -> None:
        self.history_list.clear()
        for item in self._history_items:
            list_item = QListWidgetItem(self._history_label(item))
            list_item.setData(ROLE_ITEM_ID, item.id)
            list_item.setToolTip(_history_tooltip(item))
            self.history_list.addItem(list_item)
        self._filter_history_items(self._filter_texts.get(DRAWER_HISTORY, '').strip().lower())
        self._restore_history_scroll_position()

    def _save_history_scroll_position(self) -> None:
        if not self._active_history_tab_id:
            return
        self._history_scroll_positions[self._active_history_tab_id] = (
            self.history_list.verticalScrollBar().value()
        )

    def _restore_history_scroll_position(self) -> None:
        if not self._active_history_tab_id:
            self.history_list.verticalScrollBar().setValue(0)
            return
        value = self._history_scroll_positions.get(self._active_history_tab_id, 0)
        self.history_list.verticalScrollBar().setValue(value)

    @staticmethod
    def _history_label(item: CommandHistoryItem) -> str:
        first_line = item.command.splitlines()[0] if item.command else ''
        return first_line[:120]

    def _session_to_tree(self, session: SessionItem) -> QTreeWidgetItem:
        tree_item = QTreeWidgetItem([session.name])
        tree_item.setData(0, ROLE_ITEM_ID, session.id)
        if session.is_folder():
            tree_item.setData(0, ROLE_TYPE, ITEM_TYPE_FOLDER)
            tree_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            font = tree_item.font(0)
            font.setBold(True)
            tree_item.setFont(0, font)
            for child in session.children:
                tree_item.addChild(self._session_to_tree(child))
        else:
            tree_item.setData(0, ROLE_TYPE, ITEM_TYPE_SESSION)
            tree_item.setToolTip(0, _session_tooltip(session))
        return tree_item

    def _command_to_tree(self, command: CommandItem) -> QTreeWidgetItem:
        tree_item = QTreeWidgetItem([command.name])
        tree_item.setData(0, ROLE_ITEM_ID, command.id)
        if command.is_folder():
            tree_item.setData(0, ROLE_TYPE, ITEM_TYPE_FOLDER)
            tree_item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            font = tree_item.font(0)
            font.setBold(True)
            tree_item.setFont(0, font)
            for child in command.children:
                tree_item.addChild(self._command_to_tree(child))
        else:
            tree_item.setData(0, ROLE_TYPE, ITEM_TYPE_COMMAND)
            tree_item.setToolTip(0, _command_tooltip(command))
        return tree_item

    def _sync_data_model(self) -> None:
        lookup: Dict[str, SessionItem] = {}
        self._collect_by_id(self._items, lookup)

        new_items: List[SessionItem] = []
        for i in range(self.tree.topLevelItemCount()):
            new_items.append(self._tree_to_session(self.tree.topLevelItem(i), lookup))
        self._items = new_items
        self._save_sessions()
        self.tree.viewport().update()

    @staticmethod
    def _collect_by_id(items: List[SessionItem], out: Dict[str, SessionItem]) -> None:
        for item in items:
            out[item.id] = item
            SidePanel._collect_by_id(item.children, out)

    def _tree_to_session(self, tree_item: QTreeWidgetItem,
                         lookup: Dict[str, SessionItem]) -> SessionItem:
        item_id = tree_item.data(0, ROLE_ITEM_ID)
        name = tree_item.text(0)
        item_type = tree_item.data(0, ROLE_TYPE)
        existing = lookup.get(item_id)

        if item_type == ITEM_TYPE_FOLDER:
            children: List[SessionItem] = []
            for i in range(tree_item.childCount()):
                children.append(self._tree_to_session(tree_item.child(i), lookup))
            if existing is not None and existing.is_folder():
                existing.name = name
                existing.children = children
                return existing
            return SessionItem(id=item_id, name=name, children=children)

        if existing is not None and not existing.is_folder():
            existing.name = name
            return existing
        return SessionItem(id=item_id, name=name)

    def _find_session_by_tree(self, tree_item: QTreeWidgetItem) -> Optional[SessionItem]:
        path: List[int] = []
        node: Optional[QTreeWidgetItem] = tree_item
        while node is not None:
            parent = node.parent()
            if parent:
                path.append(parent.indexOfChild(node))
            else:
                path.append(self.tree.indexOfTopLevelItem(node))
            node = parent
        path.reverse()

        if not path:
            return None

        items: List[SessionItem] = self._items
        for idx in path[:-1]:
            if 0 <= idx < len(items) and items[idx].is_folder():
                items = items[idx].children
            else:
                return None
        last = path[-1]
        if 0 <= last < len(items):
            return items[last]
        return None

    def current_session(self) -> Optional[SessionItem]:
        item = self.tree.currentItem()
        if item is None:
            return None
        session = self._find_session_by_tree(item)
        return session if session is not None and not session.is_folder() else None

    def _find_command_by_tree(self, tree_item: QTreeWidgetItem) -> Optional[CommandItem]:
        path: List[int] = []
        node: Optional[QTreeWidgetItem] = tree_item
        while node is not None:
            parent = node.parent()
            if parent:
                path.append(parent.indexOfChild(node))
            else:
                path.append(self.commands_tree.indexOfTopLevelItem(node))
            node = parent
        path.reverse()

        items: List[CommandItem] = self._commands
        for idx in path[:-1]:
            if 0 <= idx < len(items) and items[idx].is_folder():
                items = items[idx].children
            else:
                return None
        if path and 0 <= path[-1] < len(items):
            return items[path[-1]]
        return None

    def _sync_command_model(self) -> None:
        lookup: Dict[str, CommandItem] = {}
        self._collect_commands_by_id(self._commands, lookup)

        new_items: List[CommandItem] = []
        for i in range(self.commands_tree.topLevelItemCount()):
            new_items.append(self._tree_to_command(self.commands_tree.topLevelItem(i), lookup))
        self._commands = new_items
        self._save_commands()
        self.commands_tree.viewport().update()

    @staticmethod
    def _collect_commands_by_id(items: List[CommandItem], out: Dict[str, CommandItem]) -> None:
        for item in items:
            out[item.id] = item
            SidePanel._collect_commands_by_id(item.children, out)

    def _tree_to_command(self, tree_item: QTreeWidgetItem,
                         lookup: Dict[str, CommandItem]) -> CommandItem:
        item_id = tree_item.data(0, ROLE_ITEM_ID)
        name = tree_item.text(0)
        item_type = tree_item.data(0, ROLE_TYPE)
        existing = lookup.get(item_id)

        if item_type == ITEM_TYPE_FOLDER:
            children: List[CommandItem] = []
            for i in range(tree_item.childCount()):
                children.append(self._tree_to_command(tree_item.child(i), lookup))
            if existing is not None and existing.is_folder():
                existing.name = name
                existing.children = children
                return existing
            return CommandItem(id=item_id, name=name, children=children)

        if existing is not None and not existing.is_folder():
            existing.name = name
            return existing
        return CommandItem(id=item_id, name=name)

    def _on_tree_item_moved(self, _item: QTreeWidgetItem) -> None:
        clip_text = QApplication.clipboard().text()
        if clip_text.startswith(f'internal_move_tree_item_{self.tree.tree_id}='):
            QApplication.clipboard().clear()
        self._sync_data_model()
        self.sessions_changed.emit()

    def _on_command_tree_item_moved(self, _item: QTreeWidgetItem) -> None:
        clip_text = QApplication.clipboard().text()
        if clip_text.startswith(f'internal_move_tree_item_{self.commands_tree.tree_id}='):
            QApplication.clipboard().clear()
        self._sync_command_model()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, ROLE_TYPE) != ITEM_TYPE_SESSION:
            return
        session = self._find_session_by_tree(item)
        if session is not None and not session.is_folder():
            self.session_connect_requested.emit(session)

    def _on_command_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, ROLE_TYPE) != ITEM_TYPE_COMMAND:
            return
        command = self._find_command_by_tree(item)
        if command is not None and not command.is_folder():
            self.command_send_requested.emit(command.command)

    def _on_history_item_double_clicked(self, item: QListWidgetItem) -> None:
        history = self._find_history_by_id(item.data(ROLE_ITEM_ID))
        if history is not None:
            self.command_send_requested.emit(history.command)

    def _on_history_item_clicked(self, item: QListWidgetItem) -> None:
        history = self._find_history_by_id(item.data(ROLE_ITEM_ID))
        if history is not None:
            self.history_jump_requested.emit(
                history.command,
                history.sent_at,
                history.command_start_row,
            )

    def _find_history_by_id(self, item_id: str) -> Optional[CommandHistoryItem]:
        for item in self._history_items:
            if item.id == item_id:
                return item
        return None

    @staticmethod
    def _is_folder(item: QTreeWidgetItem) -> bool:
        return item.data(0, ROLE_TYPE) == ITEM_TYPE_FOLDER

    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = ShortcutMenu(self)

        if item is None:
            add_folder = menu.addAction(tr('sessions.add_folder'))
            add_menu_key(menu, add_folder, Qt.Key_F)
            add_session = menu.addAction(tr('sessions.add_session'))
            add_menu_key(menu, add_session, Qt.Key_N)
            has_items = self.tree.topLevelItemCount() > 0
            if has_items:
                menu.addSeparator()
                expand_all_action = menu.addAction(tr('sessions.expand_all'))
                add_menu_key(menu, expand_all_action, Qt.Key_A)
                collapse_all_action = menu.addAction(tr('sessions.collapse_all'))
                add_menu_key(menu, collapse_all_action, Qt.Key_L)

            action = exec_menu(menu, self.tree.viewport().mapToGlobal(pos))
            if action == add_folder:
                self._add_top_level_folder()
            elif action == add_session:
                self._add_session_to_parent(None)
            elif has_items:
                if action == expand_all_action:
                    self._expand_all()
                elif action == collapse_all_action:
                    self._collapse_all()
            return

        is_folder = self._is_folder(item)

        if is_folder:
            add_folder_action = menu.addAction(tr('sessions.add_folder'))
            add_menu_key(menu, add_folder_action, Qt.Key_F)
            add_session_action = menu.addAction(tr('sessions.add_session'))
            add_menu_key(menu, add_session_action, Qt.Key_N)
            menu.addSeparator()
            expand_action = menu.addAction(tr('sessions.expand_all'))
            add_menu_key(menu, expand_action, Qt.Key_A)
            collapse_action = menu.addAction(tr('sessions.collapse_all'))
            add_menu_key(menu, collapse_action, Qt.Key_L)
            menu.addSeparator()
            connect_action = None
            edit_action = None
        else:
            connect_action = menu.addAction(tr('sessions.connect'))
            add_menu_key(menu, connect_action, Qt.Key_C)
            edit_action = menu.addAction(tr('sessions.edit'))
            add_menu_key(menu, edit_action, Qt.Key_E)
            session = self._find_session_by_tree(item)
            forget_host_key_action = None
            if session is not None and self.host_keys.has(session.host, session.port):
                forget_host_key_action = menu.addAction(tr('sessions.forget_host_key'))
            add_folder_action = None
            add_session_action = None
            expand_action = None
            collapse_action = None
            menu.addSeparator()
        rename_action = menu.addAction(tr('sessions.rename'))
        add_menu_key(menu, rename_action, Qt.Key_R)
        delete_action = menu.addAction(tr('sessions.delete'))
        add_menu_key(menu, delete_action, Qt.Key_D)
        menu.addSeparator()

        cut_action = menu.addAction(tr('sessions.cut'))
        add_menu_key(menu, cut_action, Qt.Key_X)
        paste_action = None
        clip_text = QApplication.clipboard().text()
        prefix = f'internal_move_tree_item_{self.tree.tree_id}='
        if clip_text.startswith(prefix) and is_folder:
            try:
                source_path = json.loads(clip_text[len(prefix):])
                source_item = self.tree.get_item_by_path(source_path)
                paste_action = menu.addAction(tr('sessions.paste'))
                add_menu_key(menu, paste_action, Qt.Key_V)
                if source_item is None or source_item is item or self.tree._is_descendant_of(item, source_item):
                    paste_action.setEnabled(False)
            except (json.JSONDecodeError, TypeError):
                pass

        action = exec_menu(menu, self.tree.viewport().mapToGlobal(pos))
        if action is None:
            return

        if action == cut_action:
            self._cut_item(item)
            return
        if paste_action is not None and action == paste_action:
            self._paste_item(item)
            return

        if is_folder:
            if action == add_folder_action:
                self._add_folder_under(item)
            elif action == add_session_action:
                self._add_session_to_parent(item)
            elif action == expand_action:
                self._expand_recursive(item)
            elif action == collapse_action:
                self._collapse_recursive(item)
            elif action == rename_action:
                self._rename_item(item)
            elif action == delete_action:
                self._delete_item(item, confirm=True)
        else:
            if action == connect_action:
                self._on_item_double_clicked(item, 0)
            elif action == edit_action:
                self._edit_session(item)
            elif forget_host_key_action is not None and action == forget_host_key_action:
                self._forget_host_key(item)
            elif action == rename_action:
                self._rename_item(item)
            elif action == delete_action:
                self._delete_item(item, confirm=True)

    def _forget_host_key(self, item: QTreeWidgetItem) -> None:
        session = self._find_session_by_tree(item)
        if session is None or session.is_folder():
            return
        fingerprint = self.host_keys.fingerprint(session.host, session.port)
        if not ask_yes_no(
            self,
            tr('sessions.forget_host_key'),
            tr(
                'sessions.forget_host_key_confirm',
                host=session.host,
                port=session.port,
                fingerprint=fingerprint,
            ),
        ):
            return
        if not self.host_keys.forget(session.host, session.port):
            message_warning(self, tr('storage.save_failed_title'), tr('storage.save_host_keys_failed'))

    def _show_command_context_menu(self, pos) -> None:
        item = self.commands_tree.itemAt(pos)
        menu = ShortcutMenu(self)

        if item is None:
            add_folder = menu.addAction(tr('commands.add_folder'))
            add_menu_key(menu, add_folder, Qt.Key_F)
            add_command = menu.addAction(tr('commands.add_command'))
            add_menu_key(menu, add_command, Qt.Key_N)
            has_items = self.commands_tree.topLevelItemCount() > 0
            if has_items:
                menu.addSeparator()
                expand_all_action = menu.addAction(tr('sessions.expand_all'))
                add_menu_key(menu, expand_all_action, Qt.Key_A)
                collapse_all_action = menu.addAction(tr('sessions.collapse_all'))
                add_menu_key(menu, collapse_all_action, Qt.Key_L)

            action = exec_menu(menu, self.commands_tree.viewport().mapToGlobal(pos))
            if action == add_folder:
                self._add_top_level_command_folder()
            elif action == add_command:
                self._add_command_to_parent(None)
            elif has_items:
                if action == expand_all_action:
                    self._expand_command_all()
                elif action == collapse_all_action:
                    self._collapse_command_all()
            return

        is_folder = self._is_folder(item)

        if is_folder:
            add_folder_action = menu.addAction(tr('commands.add_folder'))
            add_menu_key(menu, add_folder_action, Qt.Key_F)
            add_command_action = menu.addAction(tr('commands.add_command'))
            add_menu_key(menu, add_command_action, Qt.Key_N)
            menu.addSeparator()
            expand_action = menu.addAction(tr('sessions.expand_all'))
            add_menu_key(menu, expand_action, Qt.Key_A)
            collapse_action = menu.addAction(tr('sessions.collapse_all'))
            add_menu_key(menu, collapse_action, Qt.Key_L)
            menu.addSeparator()
            send_action = None
            edit_action = None
        else:
            send_action = menu.addAction(tr('commands.send'))
            add_menu_key(menu, send_action, Qt.Key_S)
            edit_action = menu.addAction(tr('sessions.edit'))
            add_menu_key(menu, edit_action, Qt.Key_E)
            add_folder_action = None
            add_command_action = None
            expand_action = None
            collapse_action = None
            menu.addSeparator()

        rename_action = menu.addAction(tr('sessions.rename'))
        add_menu_key(menu, rename_action, Qt.Key_R)
        delete_action = menu.addAction(tr('sessions.delete'))
        add_menu_key(menu, delete_action, Qt.Key_D)
        menu.addSeparator()

        cut_action = menu.addAction(tr('sessions.cut'))
        add_menu_key(menu, cut_action, Qt.Key_X)
        paste_action = None
        clip_text = QApplication.clipboard().text()
        prefix = f'internal_move_tree_item_{self.commands_tree.tree_id}='
        if clip_text.startswith(prefix) and is_folder:
            try:
                source_path = json.loads(clip_text[len(prefix):])
                source_item = self.commands_tree.get_item_by_path(source_path)
                paste_action = menu.addAction(tr('sessions.paste'))
                add_menu_key(menu, paste_action, Qt.Key_V)
                if source_item is None or source_item is item or self.commands_tree._is_descendant_of(item, source_item):
                    paste_action.setEnabled(False)
            except (json.JSONDecodeError, TypeError):
                pass

        action = exec_menu(menu, self.commands_tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == cut_action:
            self._cut_command_item(item)
            return
        if paste_action is not None and action == paste_action:
            self._paste_command_item(item)
            return

        if is_folder:
            if action == add_folder_action:
                self._add_command_folder_under(item)
            elif action == add_command_action:
                self._add_command_to_parent(item)
            elif action == expand_action:
                self._expand_command_recursive(item)
            elif action == collapse_action:
                self._collapse_command_recursive(item)
            elif action == rename_action:
                self._rename_command_item(item)
            elif action == delete_action:
                self._delete_command_item(item, confirm=True)
        else:
            if action == send_action:
                self._on_command_item_double_clicked(item, 0)
            elif action == edit_action:
                self._edit_command(item)
            elif action == rename_action:
                self._rename_command_item(item)
            elif action == delete_action:
                self._delete_command_item(item, confirm=True)
    def _expand_recursive(self, item: QTreeWidgetItem) -> None:
        item.setExpanded(True)
        for i in range(item.childCount()):
            child = item.child(i)
            if self._is_folder(child):
                self._expand_recursive(child)

    def _collapse_recursive(self, item: QTreeWidgetItem) -> None:
        item.setExpanded(False)
        for i in range(item.childCount()):
            child = item.child(i)
            if self._is_folder(child):
                self._collapse_recursive(child)

    def _expand_all(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self._expand_recursive(self.tree.topLevelItem(i))

    def _collapse_all(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self._collapse_recursive(self.tree.topLevelItem(i))

    def _expand_command_recursive(self, item: QTreeWidgetItem) -> None:
        item.setExpanded(True)
        for i in range(item.childCount()):
            child = item.child(i)
            if self._is_folder(child):
                self._expand_command_recursive(child)

    def _collapse_command_recursive(self, item: QTreeWidgetItem) -> None:
        item.setExpanded(False)
        for i in range(item.childCount()):
            child = item.child(i)
            if self._is_folder(child):
                self._collapse_command_recursive(child)

    def _expand_command_all(self) -> None:
        for i in range(self.commands_tree.topLevelItemCount()):
            self._expand_command_recursive(self.commands_tree.topLevelItem(i))

    def _collapse_command_all(self) -> None:
        for i in range(self.commands_tree.topLevelItemCount()):
            self._collapse_command_recursive(self.commands_tree.topLevelItem(i))

    def _add_top_level_folder(self) -> None:
        name = prompt_text(self, tr('sessions.folder_prompt_title'), tr('sessions.prompt_name'))
        if not name:
            return
        session = SessionItem(name=name)
        tree_item = self._session_to_tree(session)
        self.tree.addTopLevelItem(tree_item)
        self._items.append(session)
        self._save_sessions()
        self.sessions_changed.emit()
        self.tree.setCurrentItem(tree_item)

    def _add_top_level_command_folder(self) -> None:
        name = prompt_text(self, tr('commands.folder_prompt_title'), tr('sessions.prompt_name'))
        if not name:
            return
        command = CommandItem(name=name)
        tree_item = self._command_to_tree(command)
        self.commands_tree.addTopLevelItem(tree_item)
        self._commands.append(command)
        self._save_commands()
        self.commands_tree.setCurrentItem(tree_item)

    def _add_folder_under(self, parent_item: QTreeWidgetItem) -> None:
        name = prompt_text(self, tr('sessions.folder_prompt_title'), tr('sessions.prompt_name'))
        if not name:
            return
        session = SessionItem(name=name)
        tree_item = self._session_to_tree(session)
        parent_item.addChild(tree_item)
        parent_item.setExpanded(True)
        parent_session = self._find_session_by_tree(parent_item)
        if parent_session is not None:
            parent_session.children.append(session)
        self._save_sessions()
        self.sessions_changed.emit()
        self.tree.setCurrentItem(tree_item)

    def _add_command_folder_under(self, parent_item: QTreeWidgetItem) -> None:
        name = prompt_text(self, tr('commands.folder_prompt_title'), tr('sessions.prompt_name'))
        if not name:
            return
        command = CommandItem(name=name)
        tree_item = self._command_to_tree(command)
        parent_item.addChild(tree_item)
        parent_item.setExpanded(True)
        parent_command = self._find_command_by_tree(parent_item)
        if parent_command is not None:
            parent_command.children.append(command)
        self._save_commands()
        self.commands_tree.setCurrentItem(tree_item)

    def _add_session_to_parent(self, parent_item: Optional[QTreeWidgetItem]) -> None:
        dialog = SessionDialog(self, title=tr('sessions.dialog_title_new'))
        if dialog.exec_() != SessionDialog.Accepted:
            return
        session = dialog.get_session()
        password = dialog.get_password()
        if password:
            self._save_credential_result(self.keyring.set_password(session.id, password))
        tree_item = self._session_to_tree(session)
        if parent_item is not None:
            parent_item.addChild(tree_item)
            parent_item.setExpanded(True)
            parent_session = self._find_session_by_tree(parent_item)
            if parent_session is not None:
                parent_session.children.append(session)
        else:
            self.tree.addTopLevelItem(tree_item)
            self._items.append(session)
        self._save_sessions()
        self.sessions_changed.emit()
        self.tree.setCurrentItem(tree_item)

    def _add_command_to_parent(self, parent_item: Optional[QTreeWidgetItem]) -> None:
        dialog = CommandDialog(self, title=tr('commands.dialog_title_new'))
        if dialog.exec_() != CommandDialog.Accepted:
            return
        command = dialog.get_command()
        if command is None:
            return
        tree_item = self._command_to_tree(command)
        if parent_item is not None:
            parent_item.addChild(tree_item)
            parent_item.setExpanded(True)
            parent_command = self._find_command_by_tree(parent_item)
            if parent_command is not None:
                parent_command.children.append(command)
        else:
            self.commands_tree.addTopLevelItem(tree_item)
            self._commands.append(command)
        self._save_commands()
        self.commands_tree.setCurrentItem(tree_item)

    def _edit_session(self, item: QTreeWidgetItem) -> None:
        session = self._find_session_by_tree(item)
        if session is None or session.is_folder():
            return
        dialog = SessionDialog(
            self,
            session=session,
            title=tr('sessions.dialog_title_edit'),
            initial_password=self.keyring.get_password(session.id) or '',
        )
        if dialog.exec_() != SessionDialog.Accepted:
            return
        updated = dialog.get_session()
        session.name = updated.name
        session.host = updated.host
        session.port = updated.port
        session.username = updated.username
        session.auth_type = updated.auth_type
        session.key_path = updated.key_path
        session.local_path = updated.local_path
        session.remote_path = updated.remote_path
        password = dialog.get_password()
        if session.auth_type != AUTH_PASSWORD:
            self._save_credential_result(self.keyring.delete_password(session.id))
        elif password:
            self._save_credential_result(self.keyring.set_password(session.id, password))
        else:
            self._save_credential_result(self.keyring.delete_password(session.id))
        item.setText(0, session.name)
        item.setToolTip(0, _session_tooltip(session))
        self._save_sessions()
        self.sessions_changed.emit()

    def _edit_command(self, item: QTreeWidgetItem) -> None:
        command = self._find_command_by_tree(item)
        if command is None or command.is_folder():
            return
        dialog = CommandDialog(self, command=command, title=tr('commands.dialog_title_edit'))
        if dialog.exec_() != CommandDialog.Accepted:
            return
        updated = dialog.get_command(existing=command)
        if updated is None:
            return
        item.setText(0, updated.name)
        item.setToolTip(0, _command_tooltip(updated))
        self._save_commands()

    def _rename_item(self, item: QTreeWidgetItem) -> None:
        session = self._find_session_by_tree(item)
        if session is None:
            return
        title_key = 'sessions.folder_prompt_title' if session.is_folder() else 'sessions.session_prompt_title'
        new_name = prompt_text(
            self, tr(title_key), tr('sessions.prompt_name'),
            initial=item.text(0),
        )
        if not new_name or new_name == item.text(0):
            return
        item.setText(0, new_name)
        self._sync_data_model()
        if not session.is_folder():
            session.name = new_name
            item.setToolTip(0, _session_tooltip(session))
        self.sessions_changed.emit()

    def _rename_command_item(self, item: QTreeWidgetItem) -> None:
        command = self._find_command_by_tree(item)
        if command is None:
            return
        title_key = 'commands.folder_prompt_title' if command.is_folder() else 'commands.command_prompt_title'
        new_name = prompt_text(
            self, tr(title_key), tr('sessions.prompt_name'),
            initial=item.text(0),
        )
        if not new_name or new_name == item.text(0):
            return
        item.setText(0, new_name)
        self._sync_command_model()
        command.name = new_name
        if not command.is_folder():
            item.setToolTip(0, _command_tooltip(command))

    def _delete_credentials_recursive(self, session: SessionItem) -> bool:
        saved = True
        if session.is_folder():
            for child in session.children:
                saved = self._delete_credentials_recursive(child) and saved
            return saved
        return self.keyring.delete_password(session.id)

    def _delete_item(self, item: QTreeWidgetItem, confirm: bool = False) -> None:
        session = self._find_session_by_tree(item)
        if session is None:
            return
        if confirm:
            if not ask_yes_no(
                self,
                tr('sessions.confirm_delete'),
                tr('sessions.confirm_delete_body', name=item.text(0)),
            ):
                return
        elif session.is_folder() and session.children:
            if not ask_yes_no(
                self,
                tr('sessions.confirm_delete'),
                tr('sessions.confirm_delete_body', name=item.text(0)),
            ):
                return
        self._save_credential_result(self._delete_credentials_recursive(session))
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self._sync_data_model()
        self.sessions_changed.emit()

    def _delete_command_item(self, item: QTreeWidgetItem, confirm: bool = False) -> None:
        command = self._find_command_by_tree(item)
        if command is None:
            return
        if confirm or (command.is_folder() and command.children):
            if not ask_yes_no(
                self,
                tr('commands.confirm_delete'),
                tr('sessions.confirm_delete_body', name=item.text(0)),
            ):
                return
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            self.commands_tree.takeTopLevelItem(self.commands_tree.indexOfTopLevelItem(item))
        self._sync_command_model()

    def _cut_item(self, item: QTreeWidgetItem) -> None:
        path = self.tree.get_item_path(item)
        clip_text = f'internal_move_tree_item_{self.tree.tree_id}={json.dumps(path)}'
        QApplication.clipboard().setText(clip_text)

    def _cut_command_item(self, item: QTreeWidgetItem) -> None:
        path = self.commands_tree.get_item_path(item)
        clip_text = f'internal_move_tree_item_{self.commands_tree.tree_id}={json.dumps(path)}'
        QApplication.clipboard().setText(clip_text)

    def _paste_item(self, target_folder: QTreeWidgetItem) -> None:
        clip_text = QApplication.clipboard().text()
        prefix = f'internal_move_tree_item_{self.tree.tree_id}='
        if not clip_text.startswith(prefix):
            return
        try:
            source_path = json.loads(clip_text[len(prefix):])
        except (json.JSONDecodeError, TypeError):
            return

        source_item = self.tree.get_item_by_path(source_path)
        if source_item is None or target_folder is None:
            return
        if source_item is target_folder or self.tree._is_descendant_of(target_folder, source_item):
            return

        expanded_states = self.tree._collect_expanded(source_item)
        parent = source_item.parent()
        if parent:
            idx = parent.indexOfChild(source_item)
            moved = parent.takeChild(idx)
        else:
            idx = self.tree.indexOfTopLevelItem(source_item)
            moved = self.tree.takeTopLevelItem(idx)
        if moved is None:
            moved = source_item

        target_folder.addChild(moved)
        target_folder.setExpanded(True)
        self.tree._restore_expanded_map(moved, expanded_states)
        self._sync_data_model()
        self.sessions_changed.emit()
        self.tree.setCurrentItem(moved)

    def _paste_command_item(self, target_folder: QTreeWidgetItem) -> None:
        clip_text = QApplication.clipboard().text()
        prefix = f'internal_move_tree_item_{self.commands_tree.tree_id}='
        if not clip_text.startswith(prefix):
            return
        try:
            source_path = json.loads(clip_text[len(prefix):])
        except (json.JSONDecodeError, TypeError):
            return

        source_item = self.commands_tree.get_item_by_path(source_path)
        if source_item is None or target_folder is None:
            return
        if source_item is target_folder or self.commands_tree._is_descendant_of(target_folder, source_item):
            return

        expanded_states = self.commands_tree._collect_expanded(source_item)
        parent = source_item.parent()
        if parent:
            idx = parent.indexOfChild(source_item)
            moved = parent.takeChild(idx)
        else:
            idx = self.commands_tree.indexOfTopLevelItem(source_item)
            moved = self.commands_tree.takeTopLevelItem(idx)
        if moved is None:
            moved = source_item

        target_folder.addChild(moved)
        target_folder.setExpanded(True)
        self.commands_tree._restore_expanded_map(moved, expanded_states)
        self._sync_command_model()
        self.commands_tree.setCurrentItem(moved)

    def _on_filter_text_changed(self, text: str) -> None:
        self._filter_texts[self._active_drawer] = text
        self._apply_active_filter()

    def _apply_active_filter(self) -> None:
        text = self._filter_texts.get(self._active_drawer, '')
        keyword = text.strip().lower()
        if self._active_drawer == DRAWER_COMMANDS:
            self._filter_command_items(keyword)
            return
        if self._active_drawer == DRAWER_HISTORY:
            self._filter_history_items(keyword)
            return
        self._filter_session_items(keyword)

    def _filter_session_items(self, keyword: str) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            self._filter_tree_item(item, keyword)

    def _filter_command_items(self, keyword: str) -> None:
        for i in range(self.commands_tree.topLevelItemCount()):
            item = self.commands_tree.topLevelItem(i)
            self._filter_command_item(item, keyword)

    @staticmethod
    def _filter_tree_item(item: QTreeWidgetItem, keyword: str) -> bool:
        child_visible = False
        for i in range(item.childCount()):
            child = item.child(i)
            if SidePanel._filter_tree_item(child, keyword):
                child_visible = True
        self_match = (not keyword) or (keyword in item.text(0).lower())
        visible = self_match or child_visible
        item.setHidden(not visible)
        return visible

    def _filter_command_item(self, item: QTreeWidgetItem, keyword: str) -> bool:
        child_visible = False
        for i in range(item.childCount()):
            child = item.child(i)
            if self._filter_command_item(child, keyword):
                child_visible = True
        command = self._find_command_by_tree(item)
        haystack = item.text(0).lower()
        if command is not None and not command.is_folder():
            haystack = f"{haystack}\n{command.command.lower()}\n{command.description.lower()}"
        self_match = (not keyword) or (keyword in haystack)
        visible = self_match or child_visible
        item.setHidden(not visible)
        return visible

    def _filter_history_items(self, keyword: str) -> None:
        for index in range(self.history_list.count()):
            item = self.history_list.item(index)
            history = self._find_history_by_id(item.data(ROLE_ITEM_ID))
            haystack = ''
            if history is not None:
                haystack = f"{history.command.lower()}\n{history.sent_at.lower()}"
            item.setHidden(bool(keyword) and keyword not in haystack)

    def _clear_filter(self) -> None:
        self._filter_edit.clear()

    def apply_appearance(self) -> None:
        appearance = get_app_config().appearance
        symbol_font = QFont(appearance.terminal_font_family)
        symbol_font.setStyleHint(QFont.Monospace)
        for symbol in self._drawer_symbols.values():
            symbol.setFont(symbol_font)
        tree_font = QFont()
        tree_font.setPixelSize(appearance.session_tree_font_size_px)
        self.tree.setFont(tree_font)
        self.commands_tree.setFont(tree_font)
        self.history_list.setFont(tree_font)
        filter_font = QFont()
        filter_font.setPixelSize(appearance.filter_edit_font_size)
        self._filter_edit.setFont(filter_font)
        self._filter_edit.setFixedHeight(appearance.filter_edit_height)

    def _refresh_tooltips(self, item: Optional[QTreeWidgetItem] = None) -> None:
        if item is None:
            for i in range(self.tree.topLevelItemCount()):
                self._refresh_tooltips(self.tree.topLevelItem(i))
            return
        session = self._find_session_by_tree(item)
        if session is not None and not session.is_folder():
            item.setToolTip(0, _session_tooltip(session))
        else:
            item.setToolTip(0, '')
        for i in range(item.childCount()):
            self._refresh_tooltips(item.child(i))

    def _refresh_command_tooltips(self, item: Optional[QTreeWidgetItem] = None) -> None:
        if item is None:
            for i in range(self.commands_tree.topLevelItemCount()):
                self._refresh_command_tooltips(self.commands_tree.topLevelItem(i))
            return
        command = self._find_command_by_tree(item)
        if command is not None and not command.is_folder():
            item.setToolTip(0, _command_tooltip(command))
        else:
            item.setToolTip(0, '')
        for i in range(item.childCount()):
            self._refresh_command_tooltips(item.child(i))

    def _refresh_history_tooltips(self) -> None:
        for index in range(self.history_list.count()):
            item = self.history_list.item(index)
            history = self._find_history_by_id(item.data(ROLE_ITEM_ID))
            if history is not None:
                item.setToolTip(_history_tooltip(history))

    def retranslate_ui(self) -> None:
        self._update_drawer_texts()
        self._filter_edit.setPlaceholderText(tr(self._filter_placeholder_key()))
        self._refresh_tooltips()
        self._refresh_command_tooltips()
        self._refresh_history_tooltips()
