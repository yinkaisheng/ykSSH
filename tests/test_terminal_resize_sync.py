#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from ui.main_window import MainWindow


class TerminalResizeSyncTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def _settle_resize(self) -> None:
        self.app.processEvents()
        await asyncio.sleep(0.12)
        self.app.processEvents()
        await asyncio.sleep(0)

    async def test_terminal_geometry_changes_update_remote_pty(self) -> None:
        with patch.object(MainWindow, '_restore_session'), patch.object(MainWindow, '_save_session'):
            window = MainWindow()
            self.app.removeEventFilter(window)
            window.resize(1200, 850)
            window.show()
            tab_id, terminal = window.terminal_tabs.add_terminal_tab('resize-test')
            window._active_tab_id = tab_id
            window.file_panels.create_panel(tab_id)
            ssh = SimpleNamespace(resize=AsyncMock(), get_sftp=lambda: None)
            window.connection_manager._sessions[tab_id] = ssh
            window.connection_manager._terminals[tab_id] = terminal
            try:
                await self._settle_resize()
                changes = (
                    ('vertical', lambda: window._vertical_splitter.moveSplitter(
                        window._vertical_splitter.sizes()[0] - 100, 1,
                    )),
                    ('horizontal', lambda: window._main_splitter.moveSplitter(
                        window._main_splitter.sizes()[0] + 100, 1,
                    )),
                    ('font', lambda: terminal.apply_terminal_font(terminal.font().family(), 18)),
                )
                for name, change in changes:
                    with self.subTest(change=name):
                        ssh.resize.reset_mock()
                        before = terminal.terminal_size()
                        change()
                        await self._settle_resize()
                        self.assertNotEqual(terminal.terminal_size(), before)
                        ssh.resize.assert_awaited_once_with(*terminal.terminal_size())

                other_id, other = window.terminal_tabs.add_terminal_tab('other-tab')
                window.file_panels.create_panel(other_id)
                await self._settle_resize()
                # 非活动 Tab 的本地字体/网格可先改变，激活时必须同步其远端尺寸。
                terminal.apply_terminal_font(terminal.font().family(), 20)
                await self._settle_resize()
                ssh.resize.reset_mock()
                window.terminal_tabs.setCurrentWidget(terminal)
                await self._settle_resize()
                ssh.resize.assert_awaited_once_with(*terminal.terminal_size())
                other.removeEventFilter(window)
            finally:
                window._session_save_timer.stop()
                window._terminal_resize_timer.stop()
                terminal.removeEventFilter(window)
                window.hide()
                window.deleteLater()
                self.app.processEvents()
