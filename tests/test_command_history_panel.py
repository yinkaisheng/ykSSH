#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from storage.command_history_store import CommandHistoryStore
from ui.command_history_panel import CommandHistoryPanel


class CommandHistoryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = CommandHistoryPanel(CommandHistoryStore())

    def test_history_is_isolated_by_active_tab(self) -> None:
        self.panel.set_active_tab('tab-a')
        self.panel.add_command('tab-a', 'echo a', '10:00', 4)
        self.panel.add_command('tab-b', 'echo b', '10:01', 8)
        self.assertEqual(self.panel.count(), 1)
        self.assertEqual(self.panel.item(0).text(), 'echo a')

        self.panel.set_active_tab('tab-b')
        self.assertEqual(self.panel.count(), 1)
        self.assertEqual(self.panel.item(0).text(), 'echo b')

    def test_filter_and_dispatch_signals(self) -> None:
        sent = []
        jumped = []
        self.panel.command_send_requested.connect(
            lambda command, execute: sent.append((command, execute)),
        )
        self.panel.history_jump_requested.connect(
            lambda command, sent_at, row: jumped.append((command, sent_at, row)),
        )
        self.panel.set_active_tab('tab-a')
        self.panel.add_command('tab-a', 'git status', '10:00', 5)
        self.panel.add_command('tab-a', 'pwd', '10:01', 9)

        self.panel.apply_filter('git')
        items = {
            self.panel.item(index).text(): self.panel.item(index)
            for index in range(self.panel.count())
        }
        self.assertFalse(items['git status'].isHidden())
        self.assertTrue(items['pwd'].isHidden())

        git_item = items['git status']
        self.panel.itemClicked.emit(git_item)
        self.panel.itemDoubleClicked.emit(git_item)
        self.assertEqual(jumped, [('git status', '10:00', 5)])
        self.assertEqual(sent, [('git status', False)])


if __name__ == '__main__':
    unittest.main()
