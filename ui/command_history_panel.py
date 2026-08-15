#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-terminal command history list used by the side panel."""
from __future__ import annotations



from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QListWidget, QListWidgetItem

from i18n import tr
from models.command_history_item import CommandHistoryItem
from storage.command_history_store import CommandHistoryStore
from ui.favorite_tree_widget import ROLE_ITEM_ID
from ui.menu_shortcuts import ShortcutMenu, add_menu_key, exec_menu


def _history_tooltip(item: CommandHistoryItem) -> str:
    return '\n'.join([
        f"{tr('history.sent_at')}: {item.sent_at}",
        item.command,
    ])


class CommandHistoryPanel(QListWidget):
    """Own history storage, tab isolation, filtering, and dispatch actions."""

    command_send_requested = pyqtSignal(str, bool)
    history_jump_requested = pyqtSignal(str, str, int)

    def __init__(self, store: CommandHistoryStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._items: list[CommandHistoryItem] = []
        self._active_tab_id: str | None = None
        self._scroll_positions: dict[str, int] = {}
        self._filter_keyword = ''

        self.setObjectName('CommandHistoryList')
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)

    def reload(self) -> None:
        self._items = self._store.load_for_tab(self._active_tab_id)
        self._rebuild()

    def set_active_tab(self, tab_id: str | None) -> None:
        self._save_scroll_position()
        self._active_tab_id = tab_id
        self.reload()

    def add_command(
        self,
        tab_id: str,
        command: str,
        sent_at: str,
        command_start_row: int,
    ) -> None:
        items = self._store.add(tab_id, command, sent_at, command_start_row)
        if tab_id == self._active_tab_id:
            self._save_scroll_position()
            self._items = items
            self._rebuild()

    def remove_tab(self, tab_id: str) -> None:
        self._scroll_positions.pop(tab_id, None)
        self._store.remove_tab(tab_id)
        if tab_id == self._active_tab_id:
            self._items = []
            self._rebuild()

    def apply_filter(self, keyword: str) -> None:
        self._filter_keyword = keyword.strip().lower()
        for index in range(self.count()):
            list_item = self.item(index)
            history = self._find_by_id(list_item.data(ROLE_ITEM_ID))
            haystack = ''
            if history is not None:
                haystack = f"{history.command.lower()}\n{history.sent_at.lower()}"
            list_item.setHidden(bool(self._filter_keyword) and self._filter_keyword not in haystack)

    def retranslate_ui(self) -> None:
        for index in range(self.count()):
            list_item = self.item(index)
            history = self._find_by_id(list_item.data(ROLE_ITEM_ID))
            if history is not None:
                list_item.setToolTip(_history_tooltip(history))

    def _rebuild(self) -> None:
        self.clear()
        for history in self._items:
            list_item = QListWidgetItem(self._history_label(history))
            list_item.setData(ROLE_ITEM_ID, history.id)
            list_item.setToolTip(_history_tooltip(history))
            self.addItem(list_item)
        self.apply_filter(self._filter_keyword)
        self._restore_scroll_position()

    def _save_scroll_position(self) -> None:
        if self._active_tab_id:
            self._scroll_positions[self._active_tab_id] = self.verticalScrollBar().value()

    def _restore_scroll_position(self) -> None:
        value = self._scroll_positions.get(self._active_tab_id or '', 0)
        self.verticalScrollBar().setValue(value)

    @staticmethod
    def _history_label(item: CommandHistoryItem) -> str:
        first_line = item.command.splitlines()[0] if item.command else ''
        return first_line[:120]

    def _find_by_id(self, item_id: str) -> CommandHistoryItem | None:
        return next((item for item in self._items if item.id == item_id), None)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        history = self._find_by_id(item.data(ROLE_ITEM_ID))
        if history is not None:
            self.command_send_requested.emit(history.command, False)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        history = self._find_by_id(item.data(ROLE_ITEM_ID))
        if history is not None:
            self.history_jump_requested.emit(
                history.command,
                history.sent_at,
                history.command_start_row,
            )

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        history = self._find_by_id(item.data(ROLE_ITEM_ID))
        if history is None:
            return

        menu = ShortcutMenu(self)
        send_action = menu.addAction(tr('commands.send'))
        add_menu_key(menu, send_action, Qt.Key_S)
        send_exec_action = menu.addAction(tr('commands.send_and_execute'))
        add_menu_key(menu, send_exec_action, Qt.Key_T)
        copy_action = menu.addAction(tr('commands.copy_command'))
        add_menu_key(menu, copy_action, Qt.Key_C)
        copy_with_time_action = menu.addAction(tr('history.copy_command_and_sent_time'))
        add_menu_key(menu, copy_with_time_action, Qt.Key_A)
        action = exec_menu(menu, self.viewport().mapToGlobal(pos))

        if action == send_action:
            self.command_send_requested.emit(history.command, False)
        elif action == send_exec_action:
            self.command_send_requested.emit(history.command, True)
        elif action == copy_action and history.command:
            QApplication.clipboard().setText(history.command)
        elif action == copy_with_time_action:
            QApplication.clipboard().setText(f'{history.sent_at}\n{history.command}')
