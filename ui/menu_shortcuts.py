#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional

from PyQt5.QtGui import QKeyEvent, QKeySequence
from PyQt5.QtWidgets import QAction, QMenu, QWidget


class ShortcutMenu(QMenu):
    """QMenu with explicit single-key action shortcuts while the menu is open."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._key_actions: dict[int, QAction] = {}
        self._shortcut_action: Optional[QAction] = None

    def add_key_action(self, key: int, action: QAction) -> None:
        self._key_actions[key] = action
        action.setShortcut(QKeySequence(key))

    def exec_with_shortcuts(self, pos) -> Optional[QAction]:
        chosen = self.exec_(pos)
        return chosen or self._shortcut_action

    def keyPressEvent(self, event: QKeyEvent) -> None:
        action = self._key_actions.get(event.key())
        if action is not None and action.isEnabled():
            self._shortcut_action = action
            action.trigger()
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)


def add_menu_key(menu: QMenu, action: QAction, key: int) -> QAction:
    """Show and handle a single-key shortcut for ShortcutMenu instances."""
    action.setShortcut(QKeySequence(key))
    if isinstance(menu, ShortcutMenu):
        menu.add_key_action(key, action)
    return action


def exec_menu(menu: QMenu, pos) -> Optional[QAction]:
    if isinstance(menu, ShortcutMenu):
        return menu.exec_with_shortcuts(pos)
    return menu.exec_(pos)


__all__ = ["ShortcutMenu", "add_menu_key", "exec_menu"]
