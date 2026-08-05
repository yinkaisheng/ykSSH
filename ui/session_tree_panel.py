#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sessions sidebar panel — tree of SSH session groups."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QKeyEvent
from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QMenu,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from models.session_item import AUTH_PASSWORD, AUTH_PUBLIC_KEY, SessionItem
from storage.app_config import get_app_config
from storage.credential_store import CredentialStore
from storage.session_profile_store import SessionProfileStore
from ui.dialog_i18n import ask_yes_no
from ui.prompt_dialog import prompt_text
from ui.favorite_tree_widget import (
    FavoriteTreeWidget,
    ITEM_TYPE_FOLDER,
    ITEM_TYPE_SESSION,
    ROLE_ITEM_ID,
    ROLE_TYPE,
)
from ui.session_dialog import SessionDialog


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


class SessionTreePanel(QWidget):
    """Tree view of saved SSH sessions with context menus."""

    session_connect_requested = pyqtSignal(SessionItem)
    sessions_changed = pyqtSignal()

    def __init__(
        self,
        session_store: SessionProfileStore,
        credential_store: Optional[CredentialStore] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.store = session_store
        self.keyring = credential_store or CredentialStore()
        self._items: List[SessionItem] = []
        self._init_ui()
        self.reload()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._title_label = QLabel(tr('sessions.title'))
        self._title_label.setObjectName('panelTitle')
        layout.addWidget(self._title_label)

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

        self._filter_edit = QLineEdit()
        self._filter_edit.setObjectName('SessionFilterEdit')
        self._filter_edit.setPlaceholderText(tr('sessions.filter_placeholder'))
        self._filter_edit.setClearButtonEnabled(False)
        self._filter_edit.textChanged.connect(self._on_filter_text_changed)
        layout.addWidget(self._filter_edit)
        self.apply_appearance()

        self.tree.installEventFilter(self)
        self.tree.viewport().installEventFilter(self)
        self._filter_edit.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.KeyPress and isinstance(event, QKeyEvent):
            if (
                obj in (self.tree, self._filter_edit)
                and event.key() == Qt.Key_Escape
                and self._filter_edit.text().strip()
            ):
                self._clear_filter()
                return True

            if obj in (self.tree, self.tree.viewport()) and self._begin_filter_from_tree(event):
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

    def persist_sessions(self) -> None:
        """Write current in-memory session tree (including favorites) to disk."""
        self.store.save_items(self._items)

    def _rebuild_tree(self) -> None:
        self.tree.clear()
        for item in self._items:
            self.tree.addTopLevelItem(self._session_to_tree(item))
        self.tree.expandAll()
        self.tree.viewport().update()

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

    def _sync_data_model(self) -> None:
        lookup: Dict[str, SessionItem] = {}
        self._collect_by_id(self._items, lookup)

        new_items: List[SessionItem] = []
        for i in range(self.tree.topLevelItemCount()):
            new_items.append(self._tree_to_session(self.tree.topLevelItem(i), lookup))
        self._items = new_items
        self.store.save_items(self._items)
        self.tree.viewport().update()

    @staticmethod
    def _collect_by_id(items: List[SessionItem], out: Dict[str, SessionItem]) -> None:
        for item in items:
            out[item.id] = item
            SessionTreePanel._collect_by_id(item.children, out)

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

    def _on_tree_item_moved(self, _item: QTreeWidgetItem) -> None:
        clip_text = QApplication.clipboard().text()
        if clip_text.startswith(f'internal_move_tree_item_{self.tree.tree_id}='):
            QApplication.clipboard().clear()
        self._sync_data_model()
        self.sessions_changed.emit()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, ROLE_TYPE) != ITEM_TYPE_SESSION:
            return
        session = self._find_session_by_tree(item)
        if session is not None and not session.is_folder():
            self.session_connect_requested.emit(session)

    @staticmethod
    def _is_folder(item: QTreeWidgetItem) -> bool:
        return item.data(0, ROLE_TYPE) == ITEM_TYPE_FOLDER

    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)

        if item is None:
            add_folder = menu.addAction(tr('sessions.add_folder'))
            add_session = menu.addAction(tr('sessions.add_session'))
            has_items = self.tree.topLevelItemCount() > 0
            if has_items:
                menu.addSeparator()
                expand_all_action = menu.addAction(tr('sessions.expand_all'))
                collapse_all_action = menu.addAction(tr('sessions.collapse_all'))

            action = menu.exec_(self.tree.viewport().mapToGlobal(pos))
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
            add_session_action = menu.addAction(tr('sessions.add_session'))
            menu.addSeparator()
            expand_action = menu.addAction(tr('sessions.expand_all'))
            collapse_action = menu.addAction(tr('sessions.collapse_all'))
            menu.addSeparator()
            connect_action = None
            edit_action = None
        else:
            connect_action = menu.addAction(tr('sessions.connect'))
            edit_action = menu.addAction(tr('sessions.edit'))
            add_folder_action = None
            add_session_action = None
            expand_action = None
            collapse_action = None
            menu.addSeparator()
        rename_action = menu.addAction(tr('sessions.rename'))
        delete_action = menu.addAction(tr('sessions.delete'))
        menu.addSeparator()

        cut_action = menu.addAction(tr('sessions.cut'))
        paste_action = None
        clip_text = QApplication.clipboard().text()
        prefix = f'internal_move_tree_item_{self.tree.tree_id}='
        if clip_text.startswith(prefix) and is_folder:
            try:
                source_path = json.loads(clip_text[len(prefix):])
                source_item = self.tree.get_item_by_path(source_path)
                paste_action = menu.addAction(tr('sessions.paste'))
                if source_item is None or source_item is item or self.tree._is_descendant_of(item, source_item):
                    paste_action.setEnabled(False)
            except (json.JSONDecodeError, TypeError):
                pass

        action = menu.exec_(self.tree.viewport().mapToGlobal(pos))
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
            elif action == rename_action:
                self._rename_item(item)
            elif action == delete_action:
                self._delete_item(item, confirm=True)

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

    def _add_top_level_folder(self) -> None:
        name = prompt_text(self, tr('sessions.folder_prompt_title'), tr('sessions.prompt_name'))
        if not name:
            return
        session = SessionItem(name=name)
        tree_item = self._session_to_tree(session)
        self.tree.addTopLevelItem(tree_item)
        self._items.append(session)
        self.store.save_items(self._items)
        self.sessions_changed.emit()
        self.tree.setCurrentItem(tree_item)

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
        self.store.save_items(self._items)
        self.sessions_changed.emit()
        self.tree.setCurrentItem(tree_item)

    def _add_session_to_parent(self, parent_item: Optional[QTreeWidgetItem]) -> None:
        dialog = SessionDialog(self, title=tr('sessions.dialog_title_new'))
        if dialog.exec_() != SessionDialog.Accepted:
            return
        session = dialog.get_session()
        password = dialog.get_password()
        if password:
            self.keyring.set_password(session.id, password)
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
        self.store.save_items(self._items)
        self.sessions_changed.emit()
        self.tree.setCurrentItem(tree_item)

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
            self.keyring.delete_password(session.id)
        elif password:
            self.keyring.set_password(session.id, password)
        else:
            self.keyring.delete_password(session.id)
        item.setText(0, session.name)
        item.setToolTip(0, _session_tooltip(session))
        self.store.save_items(self._items)
        self.sessions_changed.emit()

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

    def _delete_credentials_recursive(self, session: SessionItem) -> None:
        if session.is_folder():
            for child in session.children:
                self._delete_credentials_recursive(child)
            return
        self.keyring.delete_password(session.id)

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
        self._delete_credentials_recursive(session)
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self._sync_data_model()
        self.sessions_changed.emit()

    def _cut_item(self, item: QTreeWidgetItem) -> None:
        path = self.tree.get_item_path(item)
        clip_text = f'internal_move_tree_item_{self.tree.tree_id}={json.dumps(path)}'
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

    def _on_filter_text_changed(self, text: str) -> None:
        keyword = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            self._filter_tree_item(item, keyword)

    @staticmethod
    def _filter_tree_item(item: QTreeWidgetItem, keyword: str) -> bool:
        child_visible = False
        for i in range(item.childCount()):
            child = item.child(i)
            if SessionTreePanel._filter_tree_item(child, keyword):
                child_visible = True
        self_match = (not keyword) or (keyword in item.text(0).lower())
        visible = self_match or child_visible
        item.setHidden(not visible)
        return visible

    def _clear_filter(self) -> None:
        self._filter_edit.clear()

    def apply_appearance(self) -> None:
        appearance = get_app_config().appearance
        tree_font = QFont()
        tree_font.setPixelSize(appearance.session_tree_font_size_px)
        self.tree.setFont(tree_font)
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

    def retranslate_ui(self) -> None:
        self._title_label.setText(tr('sessions.title'))
        self._filter_edit.setPlaceholderText(tr('sessions.filter_placeholder'))
        self._refresh_tooltips()
