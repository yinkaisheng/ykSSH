#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from ui.terminal_vt_widget import TerminalVTWidget


class TerminalReconnectDirectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.terminal = TerminalVTWidget()
        self.emitted: list[bytes] = []
        self.reported: list[str] = []
        self.terminal.input_received.connect(self.emitted.append)
        self.terminal.working_directory_reported.connect(self.reported.append)

    def tearDown(self) -> None:
        self.terminal.deleteLater()

    def test_latest_prompt_survives_running_command_and_disconnect_text(self) -> None:
        self.terminal.write_text('user@host:/old$ ')
        self.terminal.write_text('cd /terminal\r\nuser@host:/terminal$ ')
        self.terminal.write_text('sleep 60\r\n')
        self.terminal.write_text('\r\nDisconnected\r\nPress Enter to reconnect\r\n')
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '/terminal')
        self.assertEqual(self.emitted, [])
        self.assertEqual(self.reported, [], '被动跟踪不能导航文件面板')

    def test_osc_report_is_not_overwritten_by_previous_visible_prompt(self) -> None:
        self.terminal.write_text('user@host:/old$ ')
        self.terminal.write_text('\x1b]7;file://host/terminal%20directory\x07')
        self.terminal.write_text('\x1b[?1049happlication')
        self.terminal.reset_session_input_modes()
        self.terminal.write_text('\r\nDisconnected\r\n')
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '/terminal directory')

    def test_home_prompt_replaces_previous_absolute_directory(self) -> None:
        self.terminal.write_text('user@host:/old$ ')
        self.terminal.write_text('cd\r\nuser@host:~$ ')
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '~')

    def test_failed_reconnect_keeps_original_snapshot(self) -> None:
        self.terminal.write_text('\x1b]7;file://host/terminal\x07')
        self.terminal.set_reconnect_enabled(True)
        self.terminal.set_reconnect_enabled(False)
        self.terminal.write_text('Connecting\r\nFailed\r\n')
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '/terminal')

    def test_emulator_reset_keeps_known_directory(self) -> None:
        self.terminal.write_text('\x1b]7;file://host/terminal\x07')
        self.terminal._reset_emulator_state(80, 24)
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '/terminal')

    def test_unrecognized_prompt_does_not_invent_directory(self) -> None:
        self.terminal.write_text('custom prompt> ')
        self.terminal.set_reconnect_enabled(True)

        self.assertIsNone(self.terminal.reconnect_working_directory)
        self.assertEqual(self.emitted, [])

    def test_disconnect_finds_home_directory_in_earlier_output_of_same_batch(self) -> None:
        self.terminal.write_text(
            'root@optp2p-cpu-origin-11:~/origin-scripts#\r\n'
            'Connection lost\r\n\r\nDisconnected\r\nPress Enter to reconnect\r\n'
        )
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '~/origin-scripts')
        self.assertEqual(self.emitted, [])
        self.assertEqual(self.reported, [])

    def test_disconnect_searches_scrollback_for_most_recent_prompt(self) -> None:
        self.terminal.write_text(
            'user@host:/old$\r\n'
            'root@host:~/origin-scripts#\r\n'
            + 'command output\r\n' * 80
            + 'Connection lost\r\n'
        )
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '~/origin-scripts')

    def test_disconnect_history_does_not_override_explicit_directory_report(self) -> None:
        self.terminal.write_text('root@host:~/old#\r\nConnection lost\r\n')
        self.terminal.write_text('\x1b]7;file://host/actual\x07')
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '/actual')

    def test_disconnect_search_is_independent_of_scrolled_view(self) -> None:
        self.terminal._reset_emulator_state(80, 4)
        self.terminal.write_text(
            'user@host:/old$\r\n' + 'older output\r\n' * 20
            + 'root@host:~/origin-scripts#\r\nConnection lost\r\n'
        )
        self.terminal.screen.prev_page()
        self.terminal.screen.prev_page()
        self.assertTrue(self.terminal.screen.history.bottom)
        self.terminal.set_reconnect_enabled(True)

        self.assertEqual(self.terminal.reconnect_working_directory, '~/origin-scripts')

    def test_explicit_directory_query_waits_for_osc_reply(self) -> None:
        self.terminal.write_text('custom prompt> ')
        self.terminal.request_working_directory()
        self.terminal.write_text('\r\nuser@host:/old$ ')
        self.assertEqual(self.reported, [])
        self.terminal.write_text('\x1b]777;ykssh-cwd;/actual\x07')
        self.assertEqual(self.reported, ['/actual'])
