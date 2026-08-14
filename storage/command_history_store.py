#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations



from models.command_history_item import CommandHistoryItem

MAX_COMMAND_HISTORY_ITEMS = 1000


class CommandHistoryStore:
    """In-memory command history, separated by runtime tab ID."""

    def __init__(self) -> None:
        self._items_by_tab: dict[str, list[CommandHistoryItem]] = {}

    def load(self) -> dict[str, list[CommandHistoryItem]]:
        return self._items_by_tab

    def load_for_tab(self, tab_id: str | None) -> list[CommandHistoryItem]:
        if not tab_id:
            return []
        return list(self._items_by_tab.get(tab_id, []))

    def add(
        self,
        tab_id: str | None,
        command: str,
        sent_at: str,
        command_start_row: int,
    ) -> list[CommandHistoryItem]:
        if not tab_id:
            return []
        items = list(self._items_by_tab.get(tab_id, []))
        command = command.strip()
        if not command:
            return items
        items.insert(0, CommandHistoryItem(
            command=command,
            sent_at=sent_at,
            command_start_row=command_start_row,
        ))
        del items[MAX_COMMAND_HISTORY_ITEMS:]
        self._items_by_tab[tab_id] = items
        return items

    def remove_tab(self, tab_id: str | None) -> None:
        if not tab_id:
            return
        self._items_by_tab.pop(tab_id, None)
