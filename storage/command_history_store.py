#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Optional

from models.command_history_item import CommandHistoryItem

MAX_COMMAND_HISTORY_ITEMS = 1000


class CommandHistoryStore:
    """In-memory command history, separated by runtime tab ID."""

    def __init__(self) -> None:
        self._items_by_tab: Dict[str, List[CommandHistoryItem]] = {}

    def load(self) -> Dict[str, List[CommandHistoryItem]]:
        return self._items_by_tab

    def load_for_tab(self, tab_id: Optional[str]) -> List[CommandHistoryItem]:
        if not tab_id:
            return []
        return list(self._items_by_tab.get(tab_id, []))

    def add(self, tab_id: Optional[str], command: str, sent_at: str) -> List[CommandHistoryItem]:
        if not tab_id:
            return []
        items = list(self._items_by_tab.get(tab_id, []))
        command = command.strip()
        if not command:
            return items
        items.insert(0, CommandHistoryItem(command=command, sent_at=sent_at))
        del items[MAX_COMMAND_HISTORY_ITEMS:]
        self._items_by_tab[tab_id] = items
        return items

    def remove_tab(self, tab_id: Optional[str]) -> None:
        if not tab_id:
            return
        self._items_by_tab.pop(tab_id, None)
