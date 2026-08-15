#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QTabWidget, QWidget

from i18n import tr
from ui.menu_shortcuts import ShortcutMenu, add_menu_key, exec_menu
from ui.prompt_dialog import prompt_text
from ui.terminal_vt_widget import TerminalVTWidget


class TerminalTabWidget(QTabWidget):
    """Terminal tabs: draggable, double-click to close, right-click rename (in-memory only)."""

    tab_close_requested = pyqtSignal(str)
    tab_closed = pyqtSignal(str)
    terminal_added = pyqtSignal(str, object)

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setObjectName('TerminalTabWidget')
        self.setTabsClosable(False)
        self.setMovable(True)
        tab_bar = self.tabBar()
        tab_bar.setObjectName('TerminalTabBar')
        tab_bar.setDrawBase(False)
        tab_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        tab_bar.tabMoved.connect(self._rebuild_tab_id_map)
        self.tabBarDoubleClicked.connect(self._on_tab_bar_double_clicked)
        self._tab_ids: dict[int, str] = {}
        self._terminals: dict[str, TerminalVTWidget] = {}
        self._display_titles: dict[str, str] = {}
        self._hosts: dict[str, str] = {}

    def add_terminal_tab(
        self,
        title: str,
        tab_id: str | None = None,
        host: str = '',
    ) -> tuple[str, TerminalVTWidget]:
        tab_id = tab_id or uuid.uuid4().hex
        terminal = TerminalVTWidget()
        index = self.addTab(terminal, title)
        self.tabBar().setDrawBase(False)
        self._tab_ids[index] = tab_id
        self._terminals[tab_id] = terminal
        self._display_titles[tab_id] = title
        if host:
            self._hosts[tab_id] = host
        self.setCurrentIndex(index)
        self.terminal_added.emit(tab_id, terminal)
        return tab_id, terminal

    def get_current_terminal(self) -> TerminalVTWidget | None:
        widget = self.currentWidget()
        return widget if isinstance(widget, TerminalVTWidget) else None

    def get_terminal(self, tab_id: str) -> TerminalVTWidget | None:
        return self._terminals.get(tab_id)

    def set_tab_title(self, tab_id: str, title: str) -> None:
        self._display_titles[tab_id] = title
        for index, stored_id in self._tab_ids.items():
            if stored_id == tab_id:
                self.setTabText(index, title)
                return

    def close_tab(self, tab_id: str) -> None:
        for index, stored_id in list(self._tab_ids.items()):
            if stored_id == tab_id:
                self.request_close_tab(tab_id)
                return

    def request_close_tab(self, tab_id: str) -> None:
        if tab_id in self._terminals:
            self.tab_close_requested.emit(tab_id)
            return

    def force_close_tab(self, tab_id: str) -> None:
        for index, stored_id in list(self._tab_ids.items()):
            if stored_id == tab_id:
                self._close_tab_at_index(index)
                return

    def _on_tab_bar_double_clicked(self, index: int) -> None:
        if index < 0:
            return
        tab_id = self._tab_ids.get(index)
        if tab_id is not None:
            self.request_close_tab(tab_id)

    def _on_tab_context_menu(self, pos) -> None:
        index = self.tabBar().tabAt(pos)
        if index < 0:
            return
        tab_id = self._tab_ids.get(index)
        host = self._hosts.get(tab_id, '') if tab_id is not None else ''
        menu = ShortcutMenu(self)
        rename_action = menu.addAction(tr('tab.rename'))
        add_menu_key(menu, rename_action, Qt.Key_R)
        copy_host_action = None
        if host:
            copy_host_action = menu.addAction(tr('sessions.copy_host'))
            add_menu_key(menu, copy_host_action, Qt.Key_H)
        action = exec_menu(menu, self.tabBar().mapToGlobal(pos))
        if action == rename_action:
            self._rename_tab_at_index(index)
        elif copy_host_action is not None and action == copy_host_action:
            QApplication.clipboard().setText(host)

    def _rename_tab_at_index(self, index: int) -> None:
        tab_id = self._tab_ids.get(index)
        if tab_id is None:
            return
        current = self._display_titles.get(tab_id, self.tabText(index))
        new_name = prompt_text(
            self,
            tr('tab.rename_title'),
            tr('tab.rename_label'),
            current,
            allow_empty=False,
        )
        if new_name is None:
            return
        self._display_titles[tab_id] = new_name
        self.setTabText(index, new_name)

    def _close_tab_at_index(self, index: int) -> None:
        tab_id = self._tab_ids.get(index)
        self._remove_tab(index)
        if tab_id is not None:
            self.tab_closed.emit(tab_id)

    def _remove_tab(self, index: int) -> None:
        tab_id = self._tab_ids.get(index)
        widget = self.widget(index)
        self.removeTab(index)
        if tab_id is not None:
            self._terminals.pop(tab_id, None)
            self._display_titles.pop(tab_id, None)
            self._hosts.pop(tab_id, None)
        self._rebuild_tab_id_map()
        if widget is not None:
            widget.deleteLater()

    def _rebuild_tab_id_map(self) -> None:
        new_map: dict[int, str] = {}
        for i in range(self.count()):
            widget = self.widget(i)
            for tab_id, terminal in self._terminals.items():
                if terminal is widget:
                    new_map[i] = tab_id
                    break
        self._tab_ids = new_map

    def retranslate_ui(self) -> None:
        pass
