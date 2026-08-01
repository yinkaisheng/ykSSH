#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from typing import Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QTabWidget, QWidget

from i18n import tr
from ui.terminal_vt_widget import TerminalVTWidget


class TerminalTabWidget(QTabWidget):
    """Tab widget with closable terminal tabs."""

    tab_closed = pyqtSignal(str)

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setTabsClosable(True)
        self.setDocumentMode(True)
        self.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tabBarDoubleClicked.connect(self._on_tab_bar_double_clicked)
        self._tab_ids: Dict[int, str] = {}
        self._terminals: Dict[str, TerminalVTWidget] = {}

    def add_terminal_tab(self, title: str, tab_id: Optional[str] = None) -> tuple[str, TerminalVTWidget]:
        tab_id = tab_id or uuid.uuid4().hex
        terminal = TerminalVTWidget()
        index = self.addTab(terminal, title)
        self._tab_ids[index] = tab_id
        self._terminals[tab_id] = terminal
        self.setCurrentIndex(index)
        return tab_id, terminal

    def get_current_terminal(self) -> Optional[TerminalVTWidget]:
        widget = self.currentWidget()
        return widget if isinstance(widget, TerminalVTWidget) else None

    def get_terminal(self, tab_id: str) -> Optional[TerminalVTWidget]:
        return self._terminals.get(tab_id)

    def set_tab_title(self, tab_id: str, title: str) -> None:
        for index, stored_id in self._tab_ids.items():
            if stored_id == tab_id:
                self.setTabText(index, title)
                return

    def close_tab(self, tab_id: str) -> None:
        for index, stored_id in list(self._tab_ids.items()):
            if stored_id == tab_id:
                self._remove_tab(index)
                return

    def _on_tab_close_requested(self, index: int) -> None:
        tab_id = self._tab_ids.get(index)
        if tab_id is not None:
            self.tab_closed.emit(tab_id)
        self._remove_tab(index)

    def _on_tab_bar_double_clicked(self, index: int) -> None:
        if index < 0:
            return
        tab_id = self._tab_ids.get(index)
        if tab_id is not None:
            self.tab_closed.emit(tab_id)
        self._remove_tab(index)

    def _remove_tab(self, index: int) -> None:
        tab_id = self._tab_ids.get(index)
        widget = self.widget(index)
        self.removeTab(index)
        if tab_id is not None:
            self._terminals.pop(tab_id, None)
        # Rebuild index -> tab_id map after removal
        new_map: Dict[int, str] = {}
        for i in range(self.count()):
            w = self.widget(i)
            for tid, term in self._terminals.items():
                if term is w:
                    new_map[i] = tid
                    break
        self._tab_ids = new_map
        if widget is not None:
            widget.deleteLater()

    def retranslate_ui(self) -> None:
        pass
