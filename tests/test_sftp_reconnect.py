#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import stat
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication

from core.connection_manager import ConnectionManager
from models.session_item import SessionItem
from ui.file_panel.panels import RemoteFilePanel
from ui.main_window import MainWindow
from ui.sftp_ui_handler import SftpUiHandler
from ui.terminal_vt_widget import TerminalVTWidget


class _RemoteDirectory:
    def __init__(self) -> None:
        self.scans: list[str] = []
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def realpath(self, _path: str) -> str:
        return '/home/test'

    async def stat(self, _path: str) -> asyncssh.SFTPAttrs:
        return asyncssh.SFTPAttrs(permissions=stat.S_IFDIR | 0o755)

    lstat = stat

    async def scandir(self, path: str):
        self.scans.append(path)
        # 模拟网络等待，使 set_path 触发的读取与初始化读取交错执行。
        self.started.set()
        await self.resume.wait()
        attrs = asyncssh.SFTPAttrs(permissions=stat.S_IFREG | 0o644)
        yield asyncssh.SFTPName(filename='reconnected.txt', attrs=attrs)


class SftpReconnectTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def _check_panel_initialization(self, *, reconnect: bool, enter: bool = False) -> None:
        tab_id = 'reconnect-tab'
        session = SessionItem(name='reconnect', host='test', remote_path='/configured')
        sftp = _RemoteDirectory()
        manager = ConnectionManager(credential_store=Mock())
        ssh = SimpleNamespace(
            get_sftp=lambda: sftp, session_item=session,
        )
        manager._sessions[tab_id] = ssh
        manager.cd_shell = AsyncMock()
        handler = SftpUiHandler(tab_id, manager, lambda: None)
        expected_path = '/last-directory' if reconnect else '/configured'
        if reconnect:
            handler.try_init_session_paths(os.path.expanduser('~'), expected_path, '/home/test')
        remote = RemoteFilePanel()
        remote.table.set_directory(expected_path)
        remote.set_sftp_handler(handler)
        remote.path_changed.connect(handler.set_remote_dir)
        remote.clear_remote()
        panel = SimpleNamespace(remote_file_panel=remote, local_file_panel=Mock())
        window = SimpleNamespace(
            connection_manager=manager,
            file_panels=SimpleNamespace(get_panel=lambda _tab: panel, show_panel=Mock()),
            _ensure_sftp_handler=lambda _tab: handler,
            _active_tab_id=tab_id,
        )
        window._attach_file_panel = MethodType(MainWindow._attach_file_panel, window)
        manager.remote_list_progress.connect(
            lambda tab, path: MainWindow._on_remote_list_progress(window, tab, path)
        )
        manager.remote_list_updated.connect(
            lambda tab, path: MainWindow._on_remote_list_updated(window, tab, path)
        )
        tasks: list[asyncio.Task] = []
        terminal = None
        if enter:
            terminal = TerminalVTWidget()
            window._tab_sessions = {tab_id: session}
            window._sftp_handlers = {tab_id: handler}
            window._tabs_ever_connected = {tab_id}
            window._connect_tasks = {}
            window._terminal_resize_timer = Mock()
            window._terminal_is_alive = MainWindow._terminal_is_alive
            window.terminal_tabs = SimpleNamespace(get_terminal=lambda _tab: terminal)
            window._confirm_host_key = AsyncMock()
            window._track_background_task = lambda task, **_kwargs: tasks.append(task)
            for method in ('_reconnect_session_async', '_open_session_on_tab', '_init_file_panel_for_session'):
                setattr(window, method, MethodType(getattr(MainWindow, method), window))

            async def open_tab(_tab_id, _session, _terminal, **kwargs) -> None:
                manager._sessions[tab_id] = ssh
                kwargs['on_connected']()

            manager._sessions.pop(tab_id)
            manager.open_tab = AsyncMock(side_effect=open_tab)
            terminal.reconnect_requested.connect(
                lambda: MainWindow._on_terminal_reconnect_requested(window, tab_id)
            )
            terminal.set_reconnect_enabled(True)
            terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        else:
            tasks.append(asyncio.create_task(MainWindow._init_file_panel_for_session(window, tab_id, session)))
        try:
            await asyncio.wait_for(sftp.started.wait(), 1)
            await asyncio.gather(*remote._background_tasks, return_exceptions=True)
            await asyncio.sleep(0)
            sftp.resume.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*remote._background_tasks, return_exceptions=True)
            await handler.wait_transfers_closed()
            self.assertEqual(
                manager._remote_cache[tab_id][expected_path][0]['name'], 'reconnected.txt',
                '确认重连后的 SFTP 已成功读到目录',
            )
            self.assertTrue(remote.placeholder.isHidden(), 'SFTP 已恢复后不能仍显示未连接占位')
            self.assertFalse(remote.table.isHidden())
            self.assertEqual(remote.path_edit.text(), expected_path)
            self.assertEqual(remote.table.rowCount(), 2)
            self.assertFalse(remote._loading)
            self.assertEqual(sftp.scans, [expected_path], '初始化只能读取一次目标目录')
            for task in tasks:
                self.assertFalse(task.cancelled(), '列表读取不能取消后续面板恢复')
                self.assertIsNone(task.exception())
            if terminal is not None:
                self.assertFalse(terminal.is_reconnect_enabled())
                manager.open_tab.assert_awaited_once()
        finally:
            for task in tasks:
                task.cancel()
            for task in list(remote._background_tasks):
                task.cancel()
            handler.cancel_transfers()
            await asyncio.gather(*tasks, *remote._background_tasks, return_exceptions=True)
            await handler.wait_transfers_closed()
            remote.deleteLater()
            if terminal is not None:
                terminal.deleteLater()

    async def test_reconnect_restores_remote_panel_after_directory_scan(self) -> None:
        await self._check_panel_initialization(reconnect=True)

    async def test_enter_reconnect_restores_remote_panel(self) -> None:
        await self._check_panel_initialization(reconnect=True, enter=True)

    async def test_initial_connection_loads_configured_directory_once(self) -> None:
        await self._check_panel_initialization(reconnect=False)
