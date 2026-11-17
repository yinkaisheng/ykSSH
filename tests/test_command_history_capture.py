#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pyte  # type: ignore
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication

from ui.terminal_vt_widget import TerminalVTWidget


class CommandHistoryCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = TerminalVTWidget()
        self.addCleanup(self.widget.close)
        self.submitted: list[str] = []
        self.widget.command_submitted.connect(
            lambda command, _sent_at, _row: self.submitted.append(command)
        )

    def _bind_screen(self, text: str, *, cols: int = 80, rows: int = 24) -> object:
        geometry = patch.object(self.widget, '_calc_cols_rows', return_value=(cols, rows))
        geometry.start()
        self.addCleanup(geometry.stop)
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
        self.widget.write_text('\r\n')
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

    def test_recalled_nohup_edited_after_tail_interrupt_is_recorded(self) -> None:
        prompt = 'optuser@sg-edge-241:~/ykstest$ '
        stream = self._bind_screen(prompt, cols=240)
        original = (
            'nohup ./s4-location-client-linux-amd64 -server 216.227.169.93:6543 '
            '-time 0 -window 2000 > client-sg-window2000.log &'
        )
        self.widget._append_pending_command_text(original)
        stream.feed(original)
        self.widget._commit_command_start(check_continuation=True)
        stream.feed('\r\n[1] 193567\r\n' + prompt)
        tail = 'tail -f client-sg-window2000.log -n 20'
        self.widget._append_pending_command_text(tail)
        stream.feed(tail)
        self.widget._commit_command_start(check_continuation=True)
        stream.feed('\r\n{"event":"progress"}\r\n')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, '\x03'))
        stream.feed('^C\r\n' + prompt)
        for recalled in (tail, original):
            self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
            stream.feed('\r\x1b[2K' + prompt + recalled)
        # 模拟在 readline 中改好参数后，最后补入后台执行符。
        edited = original.replace('2000', '0')[:-1]
        stream.feed('\r\x1b[2K' + prompt + edited)
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Ampersand, Qt.NoModifier, '&'))
        stream.feed('&')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.widget.write_text('\r\n[2] 193594\r\n' + prompt)
        self.assertEqual(self.submitted, [original, tail, edited + '&'])

    def test_recalled_command_edited_at_end_is_recorded(self) -> None:
        stream = self._bind_screen('user@host:~$ ')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        stream.feed('echo ')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.NoModifier, 'x'))
        stream.feed('x')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.widget.write_text('\r\n')
        self.assertEqual(self.submitted, ['echo x'])

    def test_recalled_command_without_edits_is_recorded(self) -> None:
        stream = self._bind_screen('user@host:~$ ')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        stream.feed('pwd')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.widget.write_text('\r\n')
        self.assertEqual(self.submitted, ['pwd'])

    def test_recall_in_repl_is_not_recorded(self) -> None:
        stream = self._bind_screen('>>> ')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        stream.feed('print(1)')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(self.submitted, [])

    def test_recalled_wrapped_nohup_is_recorded_in_full(self) -> None:
        command = (
            'nohup ./s4-location-client-linux-amd64 -server 216.227.169.93:6543 '
            '-time 0 -window 0 > client-sg-window0.log &'
        )
        for at_bottom in (False, True):
            with self.subTest(at_bottom=at_bottom), patch.object(
                self.widget, '_calc_cols_rows', return_value=(80, 5),
            ):
                self.submitted.clear()
                self.widget._reset_emulator_state(80, 5)
                self.widget.write_text(('\r\n' * 4 if at_bottom else '') + 'optuser@sg-edge-241:~/ykstest$ ')
                self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
                self.widget.write_text(command)
                self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
                self.widget.write_text('\r\n')
                self.assertEqual(self.submitted, [command])

    def test_recall_down_records_final_echo_instead_of_typed_fragment(self) -> None:
        stream = self._bind_screen('user@host:~$ ')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        stream.feed('pwd')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
        stream.feed('\r\x1b[2Kuser@host:~$ echo final')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.widget.write_text('\r\n')
        self.assertEqual(self.submitted, ['echo final'])

    def test_recall_without_remote_echo_is_not_recorded(self) -> None:
        self._bind_screen('user@host:~$ ')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.widget.write_text('\r\nuser@host:~$ ')
        self.assertEqual(self.submitted, [])

    def test_fast_typing_with_partial_remote_echo_records_full_command(self) -> None:
        for echoed_length in range(len('ifconfig')):
            with self.subTest(echoed_length=echoed_length):
                self.submitted.clear()
                self.widget._clear_command_marks()
                stream = self._bind_screen('user@host:~$ ')
                for char in 'ifconfig':
                    self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, ord(char.upper()), Qt.NoModifier, char))
                stream.feed('ifconfig'[:echoed_length])
                self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
                self.assertEqual(self.submitted, [])
                self.widget.write_text('ifconfig'[echoed_length:] + '\r\ninet 127.0.0.1\r\nuser@host:~$ ')
                self.assertEqual(self.submitted, ['ifconfig'])
                self.widget.write_text('\r\n')
                self.assertEqual(self.submitted, ['ifconfig'])

    def test_delayed_echo_in_several_chunks_waits_for_submission_line_end(self) -> None:
        stream = self._bind_screen('user@host:~$ ')
        self.widget._append_pending_command_text('ifconfig')
        stream.feed('ifcon')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        for chunk in ('f', 'i', 'g', '\r'):
            self.widget.write_text(chunk)
            self.assertEqual(self.submitted, [])
        self.widget.write_text('\n')
        self.assertEqual(self.submitted, ['ifconfig'])

    def test_delayed_tab_completion_records_completed_echo(self) -> None:
        stream = self._bind_screen('user@host:~$ ')
        self.widget._append_pending_command_text('ifcon')
        stream.feed('ifcon')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier))
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(self.submitted, [])
        self.widget.write_text('fig\r\n')
        self.assertEqual(self.submitted, ['ifconfig'])

    def test_delayed_wrapped_echo_waits_for_last_row_to_finish(self) -> None:
        with patch.object(self.widget, '_calc_cols_rows', return_value=(80, 5)):
            self.widget._reset_emulator_state(80, 5)
            self.widget.write_text('\r\n' * 4 + 'user@host:~$ ')
            command = 'echo ' + 'x' * 180
            self.widget._append_pending_command_text(command)
            self.widget.write_text(command[:100])
            self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
            self.assertEqual(self.submitted, [])
            self.widget.write_text(command[100:])
            self.assertEqual(self.submitted, [])
            self.widget.write_text('\r\noutput\r\nuser@host:~$ ')
            self.assertEqual(self.submitted, [command])

    def test_hidden_local_input_does_not_enter_history_after_newline(self) -> None:
        self._bind_screen('user@host:~$ ')
        self.widget._append_pending_command_text('hidden-input')
        self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.widget.write_text('\r\nuser@host:~$ ')
        self.assertEqual(self.submitted, [])
        self.assertEqual(self.widget._awaiting_command_echo, [])
        self.assertEqual(self.widget._command_start_commands, {})

    def test_pending_echo_is_discarded_on_disconnect_and_clear(self) -> None:
        for reset in ('disconnect', 'clear'):
            with self.subTest(reset=reset):
                self.widget.set_reconnect_enabled(False)
                stream = self._bind_screen('user@host:~$ ')
                self.widget._append_pending_command_text('ifconfig')
                stream.feed('ifcon')
                self.widget.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
                if reset == 'disconnect':
                    self.widget.set_reconnect_enabled(True)
                else:
                    self.widget._clear_command_marks()
                self.widget.write_text('fig\r\n')
                self.assertEqual(self.submitted, [])
                self.assertEqual(self.widget._awaiting_command_echo, [])
