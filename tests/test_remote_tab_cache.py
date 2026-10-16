#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import stat
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import asyncssh

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from core.connection_manager import ConnectionManager
from ui.file_panel.panels import RemoteFilePanel
from ui.main_window import MainWindow
from ui.sftp_ui_handler import SftpUiHandler


class _Directory:
    def __init__(self, *, pause: bool, block_restarts: bool = True) -> None:
        self.pause = pause
        self.block_restarts = block_restarts
        self.calls = 0
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()
        self.restart = asyncio.Event()

    async def scandir(self, _path: str):
        self.calls += 1
        scan_number = self.calls
        if scan_number > 1 and self.block_restarts:
            await self.restart.wait()
        attrs = asyncssh.SFTPAttrs(permissions=stat.S_IFREG | 0o644)
        for number in range(128):
            yield asyncssh.SFTPName(filename=f'file-{number:03d}', attrs=attrs)
        if self.pause:
            self.paused.set()
            await self.resume.wait()
        yield asyncssh.SFTPName(filename=f'last-{scan_number}', attrs=attrs)


class RemoteTabCacheTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, directory: _Directory):
        manager = ConnectionManager(credential_store=Mock())
        directories = {'tab-a': directory, 'tab-b': _Directory(pause=False)}
        panels = {}
        handlers = {}
        for tab_id, sftp in directories.items():
            manager._sessions[tab_id] = SimpleNamespace(get_sftp=lambda sftp=sftp: sftp)
            remote = RemoteFilePanel()
            remote.table.set_directory('/target')
            panels[tab_id] = SimpleNamespace(remote_file_panel=remote, local_file_panel=Mock())
            handlers[tab_id] = SftpUiHandler(tab_id, manager, lambda: None)
        window = SimpleNamespace(
            connection_manager=manager,
            file_panels=SimpleNamespace(get_panel=panels.get, show_panel=Mock(), show_empty=Mock()),
            terminal_tabs=SimpleNamespace(tab_id_at=lambda index: ('tab-a', 'tab-b')[index]),
            side_panel=SimpleNamespace(set_active_history_tab=Mock()),
            _save_active_tab_paths=Mock(),
            _ensure_sftp_handler=handlers.get,
            _active_tab_id=None,
        )
        window._attach_file_panel = MethodType(MainWindow._attach_file_panel, window)
        manager.remote_list_progress.connect(
            lambda tab, path: MainWindow._on_remote_list_progress(window, tab, path)
        )
        manager.remote_list_updated.connect(
            lambda tab, path: MainWindow._on_remote_list_updated(window, tab, path)
        )
        return window, panels, handlers

    async def _cleanup(self, panels, handlers, manager) -> None:
        for handler in handlers.values():
            handler.cancel_transfers()
        for task in list(manager._refresh_tasks.values()):
            task.cancel()
        for handler in handlers.values():
            await handler.wait_transfers_closed()
        await asyncio.gather(*manager._refresh_tasks.values(), return_exceptions=True)
        for panel in panels.values():
            panel.remote_file_panel.deleteLater()

    async def _wait_tab(self, window, handlers, tab_id: str) -> None:
        await handlers[tab_id].wait_transfers_closed()
        tasks = [task for key, task in window.connection_manager._refresh_tasks.items() if key[0] == tab_id]
        await asyncio.gather(*tasks)

    async def test_switch_back_keeps_partial_rows_and_running_scan(self) -> None:
        directory = _Directory(pause=True)
        window, panels, handlers = self._window(directory)
        try:
            MainWindow._on_current_tab_changed(window, 0)
            await asyncio.wait_for(directory.paused.wait(), 1)
            self.assertEqual(panels['tab-a'].remote_file_panel.table.rowCount(), 129)
            MainWindow._on_current_tab_changed(window, 1)
            await asyncio.sleep(0)
            MainWindow._on_current_tab_changed(window, 0)
            await asyncio.sleep(0)
            remote = panels['tab-a'].remote_file_panel
            self.assertEqual(remote.table.rowCount(), 129, '切回 Tab 必须保留已经加载的 128 个文件')
            self.assertEqual(directory.calls, 1, '切换 Tab 不能重新读取目录')
            self.assertTrue(remote._loading)
            directory.resume.set()
            await self._wait_tab(window, handlers, 'tab-a')
            self.assertEqual(remote.table.rowCount(), 130)
            self.assertFalse(remote._loading)
        finally:
            await self._cleanup(panels, handlers, window.connection_manager)

    async def test_completed_list_is_not_reloaded_on_tab_switch(self) -> None:
        directory = _Directory(pause=False)
        window, panels, handlers = self._window(directory)
        try:
            MainWindow._on_current_tab_changed(window, 0)
            await self._wait_tab(window, handlers, 'tab-a')
            first_item = panels['tab-a'].remote_file_panel.table.item(0, 0)
            MainWindow._on_current_tab_changed(window, 1)
            await asyncio.sleep(0)
            MainWindow._on_current_tab_changed(window, 0)
            await asyncio.sleep(0)
            self.assertEqual(directory.calls, 1, '已完成的目录应直接使用缓存')
            self.assertEqual(panels['tab-a'].remote_file_panel.table.rowCount(), 130)
            self.assertFalse(panels['tab-a'].remote_file_panel._loading)
            self.assertIs(panels['tab-a'].remote_file_panel.table.item(0, 0), first_item,
                          '缓存未变化时应直接保留原有表格行')
        finally:
            await self._cleanup(panels, handlers, window.connection_manager)

    async def test_background_completion_is_displayed_when_switching_back(self) -> None:
        directory = _Directory(pause=True)
        window, panels, handlers = self._window(directory)
        try:
            MainWindow._on_current_tab_changed(window, 0)
            await asyncio.wait_for(directory.paused.wait(), 1)
            MainWindow._on_current_tab_changed(window, 1)
            directory.resume.set()
            await self._wait_tab(window, handlers, 'tab-a')
            MainWindow._on_current_tab_changed(window, 0)
            await asyncio.sleep(0)
            remote = panels['tab-a'].remote_file_panel
            self.assertEqual(directory.calls, 1)
            self.assertEqual(remote.table.rowCount(), 130)
            self.assertFalse(remote._loading)
        finally:
            await self._cleanup(panels, handlers, window.connection_manager)

    async def test_manual_refresh_still_reads_fresh_remote_data(self) -> None:
        directory = _Directory(pause=False, block_restarts=False)
        window, panels, handlers = self._window(directory)
        try:
            MainWindow._on_current_tab_changed(window, 0)
            await self._wait_tab(window, handlers, 'tab-a')
            for _ in range(2):
                MainWindow._on_current_tab_changed(window, 1)
                await asyncio.sleep(0)
                MainWindow._on_current_tab_changed(window, 0)
                await asyncio.sleep(0)
            panels['tab-a'].remote_file_panel.table.refresh_requested.emit()
            await self._wait_tab(window, handlers, 'tab-a')
            self.assertEqual(directory.calls, 2)
            names = {entry['name'] for entry in window.connection_manager._remote_cache['tab-a']['/target']}
            self.assertIn('last-2', names)
            self.assertNotIn('last-1', names)
        finally:
            await self._cleanup(panels, handlers, window.connection_manager)
