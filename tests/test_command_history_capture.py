#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pyte  # type: ignore
from PyQt5.QtWidgets import QApplication

from ui.terminal_vt_widget import TerminalVTWidget


class CommandHistoryCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = TerminalVTWidget()
        self.submitted: list[str] = []
        self.widget.command_submitted.connect(
            lambda command, _sent_at, _row: self.submitted.append(command)
        )

    def _bind_screen(self, text: str, *, cols: int = 80, rows: int = 24) -> object:
        screen = pyte.HistoryScreen(cols, rows, history=20)  # type: ignore[attr-defined]
        stream = pyte.Stream(screen)  # type: ignore[attr-defined]
        stream.feed(text)
        self.widget._main_screen = screen
        self.widget._main_stream = stream
        self.widget.screen = screen
        self.widget.stream = stream
        self.widget._in_alt_screen = False
        return stream

    def test_login_prompt_command_is_recorded(self) -> None:
        stream = self._bind_screen(
            'Last login: Thu Sep 17 11:19:51 2026 from 104.223.45.141\r\n'
            'ubuntu@xjp:~$ '
        )
        self.widget._append_pending_command_text('ls')
        stream.feed('ls')
        self.widget._commit_command_start(check_continuation=True)
        self.assertEqual(self.submitted, ['ls'])

    def test_python_repl_input_is_not_recorded(self) -> None:
        stream = self._bind_screen('ubuntu@xjp:~$ python3\r\n>>> ')
        self.widget._append_pending_command_text('1*2')
        stream.feed('1*2')
        self.widget._commit_command_start(check_continuation=True)
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.widget._command_start_rows, [])

    def test_bare_dollar_prompt_is_not_recorded(self) -> None:
        stream = self._bind_screen('$ ')
        self.widget._append_pending_command_text('whoami')
        stream.feed('whoami')
        self.widget._commit_command_start(check_continuation=True)
        self.assertEqual(self.submitted, [])

    def test_backslash_continuation_from_shell_prompt_is_recorded(self) -> None:
        stream = self._bind_screen('ubuntu@xjp:~$ ')
        self.widget._append_pending_command_text('echo \\')
        stream.feed('echo \\')
        self.widget._commit_command_start(check_continuation=True)
        self.assertEqual(self.submitted, [])
        self.assertTrue(self.widget._pending_command_from_shell_prompt)

        stream.feed('\r\n> ')
        self.widget._append_pending_command_text('hello')
        stream.feed('hello')
        self.widget._commit_command_start(check_continuation=True)
        self.assertTrue(self.submitted)
        self.assertIn('echo', self.submitted[0])

    def test_backslash_continuation_without_ps2_is_recorded(self) -> None:
        stream = self._bind_screen('ubuntu@xjp:~$ ')
        self.widget._append_pending_command_text('echo \\')
        stream.feed('echo \\')
        self.widget._commit_command_start(check_continuation=True)
        self.assertEqual(self.submitted, [])

        stream.feed('\r\n')
        self.widget._append_pending_command_text('hello')
        stream.feed('hello')
        self.widget._commit_command_start(check_continuation=True)
        self.assertTrue(self.submitted)
        self.assertIn('echo', self.submitted[0])

    def test_send_and_execute_still_records_without_shell_prompt(self) -> None:
        self._bind_screen('>>> ')
        self.widget.send_command_text('uptime', execute=True)
        self.assertEqual(self.submitted, ['uptime'])
