#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from core.connection_manager import ConnectionManager
from models.session_item import SessionItem
from ui.main_window import MainWindow
from ui.terminal_tab_widget import TerminalTabWidget


class ReviewFollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_tab_id_at_tracks_tab_order(self) -> None:
        tabs = TerminalTabWidget()
        tabs.add_terminal_tab('first', tab_id='tab-a')
        tabs.add_terminal_tab('second', tab_id='tab-b')

        self.assertEqual(tabs.tab_id_at(0), 'tab-a')
        self.assertEqual(tabs.tab_id_at(1), 'tab-b')

        tabs.tabBar().moveTab(0, 1)

        self.assertEqual(tabs.tab_id_at(0), 'tab-b')
        self.assertEqual(tabs.tab_id_at(1), 'tab-a')
        self.assertIsNone(tabs.tab_id_at(-1))
        self.assertIsNone(tabs.tab_id_at(2))

    def test_remote_list_update_refreshes_only_current_path(self) -> None:
        remote_panel = Mock()
        remote_panel.current_path.return_value = '/current'
        panel = SimpleNamespace(remote_file_panel=remote_panel)
        window = SimpleNamespace(
            _active_tab_id='tab-a',
            file_panels=SimpleNamespace(get_panel=Mock(return_value=panel)),
        )

        MainWindow._on_remote_list_updated(window, 'tab-a', '/stale')
        remote_panel.refresh.assert_not_called()

        MainWindow._on_remote_list_updated(window, 'tab-a', '/current')
        remote_panel.refresh.assert_called_once_with()


class ReviewFollowupAsyncTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def test_remote_list_update_emits_tab_and_path(self) -> None:
        manager = ConnectionManager(credential_store=Mock())
        manager._sessions['tab-a'] = SimpleNamespace(get_sftp=lambda: object())
        updates: list[tuple[str, ...]] = []
        manager.remote_list_updated.connect(lambda *args: updates.append(args))

        with patch(
            'core.connection_manager.listdir',
            AsyncMock(return_value=[]),
        ):
            await manager.refresh_remote_list('tab-a', '/srv/project')

        self.assertEqual(updates, [('tab-a', '/srv/project')])

    async def test_file_panel_init_stops_when_tab_closes_during_shell_wait(self) -> None:
        panel = object()
        panels = SimpleNamespace(get_panel=Mock(return_value=panel))
        handler = Mock()
        connection_manager = SimpleNamespace(
            get_session=Mock(return_value=object()),
            resolve_remote_path=AsyncMock(side_effect=['/home/user', '/srv/project']),
            cd_shell=AsyncMock(),
        )

        async def close_tab_during_wait(_tab_id: str, _path: str) -> None:
            panels.get_panel.return_value = None
            connection_manager.get_session.return_value = None

        connection_manager.cd_shell.side_effect = close_tab_during_wait
        window = SimpleNamespace(
            file_panels=panels,
            connection_manager=connection_manager,
            _ensure_sftp_handler=Mock(return_value=handler),
            _active_tab_id='tab-a',
        )

        with patch('ui.main_window.resolve_local_path', return_value='/tmp'):
            await MainWindow._init_file_panel_for_session(
                window,
                'tab-a',
                SessionItem(remote_path='/srv/project'),
            )

        connection_manager.cd_shell.assert_awaited_once_with('tab-a', '/srv/project')
        handler.try_init_session_paths.assert_not_called()

    async def test_finish_close_closes_window_after_cleanup_failure(self) -> None:
        window = SimpleNamespace(
            _save_session=Mock(),
            _close_all_async=AsyncMock(side_effect=RuntimeError('boom')),
            _closing_after_transfer_confirm=False,
            _close_in_progress=True,
            close=Mock(),
        )

        with (
            patch('ui.main_window.logger.exception') as log_exception,
        ):
            await MainWindow._finish_close_async(window)

        self.assertTrue(window._closing_after_transfer_confirm)
        self.assertFalse(window._close_in_progress)
        window.close.assert_called_once_with()
        log_exception.assert_called_once()

    async def test_shared_connection_loss_discards_every_related_tab(self) -> None:
        manager = ConnectionManager(credential_store=Mock())
        connection = SimpleNamespace(session_id='session-a', member_count=0)
        ssh_a = SimpleNamespace(
            shared_connection=connection,
            disconnect=AsyncMock(),
            data_received=Mock(),
            disconnected=Mock(),
            error=Mock(),
            deleteLater=Mock(),
        )
        ssh_b = SimpleNamespace(
            shared_connection=connection,
            disconnect=AsyncMock(),
            data_received=Mock(),
            disconnected=Mock(),
            error=Mock(),
            deleteLater=Mock(),
        )
        manager._connections['session-a'] = connection
        manager._sessions.update({'tab-a': ssh_a, 'tab-b': ssh_b})
        manager._terminals.update({'tab-a': None, 'tab-b': None})
        manager._tab_titles.update({'tab-a': 'A', 'tab-b': 'B'})
        manager._remote_cache.update({'tab-a': {'/': []}, 'tab-b': {'/': []}})

        with patch.object(manager, '_disconnect_ssh_signals'):
            await manager._on_shared_connection_lost(connection)

        ssh_a.disconnect.assert_awaited_once_with()
        ssh_b.disconnect.assert_awaited_once_with()
        self.assertEqual(manager._connections, {})
        self.assertEqual(manager._sessions, {})
        self.assertEqual(manager._terminals, {})
        self.assertEqual(manager._tab_titles, {})
        self.assertEqual(manager._remote_cache, {})


if __name__ == '__main__':
    unittest.main()
