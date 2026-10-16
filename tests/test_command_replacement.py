# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication

from core.ssh_session import SSHSession
from models.command_item import CommandItem
from ui.main_window import MainWindow
from ui.side_panel import SidePanel
from ui.terminal_vt_widget import TerminalVTWidget


class CommandReplacementTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    async def asyncSetUp(self) -> None:
        self.terminal = TerminalVTWidget()
        self.ssh = SSHSession('tab-a')
        self.sessions = {'tab-a': self.ssh}
        self.terminals = {'tab-a': self.terminal}
        self.window = SimpleNamespace(
            _active_tab_id='tab-a', _command_fill_tasks={}, _background_tasks=set(),
            terminal_tabs=SimpleNamespace(
                get_current_terminal=lambda: self.terminals.get(self.window._active_tab_id),
                get_terminal=self.terminals.get,
            ),
            connection_manager=SimpleNamespace(get_session=self.sessions.get),
            _terminal_is_alive=MainWindow._terminal_is_alive,
        )
        for name in ('_cancel_command_fill', '_fill_command_async', '_track_background_task', '_replace_command_in_active_terminal'):
            setattr(self.window, name, MethodType(getattr(MainWindow, name), self.window))
        self.emitted: list[bytes] = []
        self.cleared = asyncio.Event()

        def receive(data):
            self.emitted.append(data)
            if data in (b'\x03', b'\x01\x0b'):
                self.cleared.set()

        self.terminal.input_received.connect(receive)

    async def asyncTearDown(self) -> None:
        for task in list(self.window._background_tasks):
            task.cancel()
        await asyncio.gather(*self.window._background_tasks, return_exceptions=True)
        for terminal in self.terminals.values():
            terminal.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def output(self, text: str) -> None:
        self.terminal.write_text(text)
        self.ssh._output_activity.set()

    def fill(self, command: str = 'git status') -> asyncio.Task:
        self.window._replace_command_in_active_terminal(command)
        return self.window._command_fill_tasks['tab-a']

    async def wait_clear(self) -> None:
        await asyncio.wait_for(self.cleared.wait(), 1)

    async def test_cancels_single_input_and_preserves_screen_history(self) -> None:
        self.output('user@host:~$ ')
        self.terminal._append_pending_command_text('old command')
        self.output('old command\x1b[5D')
        task = self.fill()
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x03'])
        self.output('\x1b[5C^C\r\nuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted, [b'\x03', b'git status'])
        self.assertIn('old command^C', '\n'.join(self.terminal.screen.display))
        submitted = []
        self.terminal.command_submitted.connect(lambda command, _time, _row: submitted.append(command))
        self.output('git status')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(submitted, ['git status'])

    async def test_quote_continuation_is_interrupted_before_filling(self) -> None:
        self.output('user@host:~$ echo "unfinished\r\n> more')
        task = self.fill('pwd')
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x03'])
        self.output('^C\r\n')
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.output('user@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted, [b'\x03', b'pwd'])
        self.assertEqual(self.terminal._pending_command_text, 'pwd')

    async def test_backslash_continuation_discards_old_history_tracking(self) -> None:
        self.output('user@host:~$ ')
        self.terminal._append_pending_command_text('echo old \\')
        self.output('echo old \\')
        self.terminal._commit_command_start(check_continuation=True)
        self.output('\r\n> ')
        self.terminal._append_pending_command_text('remaining')
        self.output('remaining')
        task = self.fill('pwd')
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x03'])
        self.output('^C\r\nuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        submitted = []
        self.terminal.command_submitted.connect(lambda command, _time, _row: submitted.append(command))
        self.output('pwd')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        self.assertEqual(submitted, ['pwd'])

    async def test_multiline_buffer_requires_fresh_output_even_on_primary_prompt(self) -> None:
        self.output('user@host:~$ ')
        self.terminal._pending_command_text = 'echo first\necho second'
        task = self.fill()
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x03'])
        self.assertFalse(task.done())
        self.output('^C\r\nuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted[-1], b'git status')

    async def test_zsh_continuation_prompts(self) -> None:
        for prompt in ('> ', 'dquote> ', 'quote> ', 'heredoc> ', 'pipe> ', 'for> '):
            with self.subTest(prompt=prompt):
                self.output('\r\x1b[2K' + prompt)
                self.assertTrue(self.terminal.prepare_command_replacement())
                self.assertEqual(self.emitted[-1], b'\x03')

    async def test_empty_prompt_fills_immediately_without_enter(self) -> None:
        self.output('user@host:~$ ')
        await asyncio.wait_for(self.fill(), 1)
        self.assertEqual(self.emitted, [b'git status'])

    async def test_whitespace_is_deleted_without_interrupt_before_filling(self) -> None:
        self.output('user@host:~$ ')
        self.terminal._append_pending_command_text(' \t\u3000 ')
        self.output(' \t\u3000 ')
        task = self.fill()
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x01\x0b'])
        self.assertFalse(task.done())
        self.output('\r\x1b[2Kuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted, [b'\x01\x0b', b'git status'])
        self.assertEqual(self.terminal._pending_command_text, 'git status')

    async def test_whitespace_after_cursor_and_wrapped_whitespace_are_deleted(self) -> None:
        for count, move in ((8, '\x1b[8D'), (180, '')):
            with self.subTest(count=count):
                self.output('\r\nuser@host:~$ ')
                self.terminal._append_pending_command_text(' ' * count)
                self.output(' ' * count + move)
                self.assertTrue(self.terminal.prepare_command_replacement())
                self.assertEqual(self.emitted[-1], b'\x01\x0b')

    async def test_keyboard_spaces_are_deleted_at_every_cursor_position(self) -> None:
        for left_count in range(7):
            with self.subTest(left_count=left_count):
                self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, '\x03'))
                self.output('^C\r\nuser@host:~$ ')
                for _ in range(6):
                    self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, ' '))
                    self.output(' ')
                for _ in range(left_count):
                    self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier))
                    self.output('\x1b[D')
                self.emitted.clear()
                self.cleared.clear()
                task = self.fill()
                await self.wait_clear()
                self.assertEqual(self.emitted, [b'\x01\x0b'])
                self.output('\r\x1b[2Kuser@host:~$ ')
                await asyncio.wait_for(task, 1)
                self.assertEqual(self.emitted, [b'\x01\x0b', b'git status'])

    async def test_whitespace_after_deleting_text_at_input_start_is_not_interrupted(self) -> None:
        self.output('user@host:~$ ')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.NoModifier, 'x'))
        self.output('x')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, ' '))
        self.output(' ')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Home, Qt.NoModifier))
        self.output('\x1b[2D')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
        self.output('\x1b[P')
        self.emitted.clear()
        task = self.fill()
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x01\x0b'])
        self.output('\r\x1b[2Kuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted, [b'\x01\x0b', b'git status'])

    async def test_spaces_after_manual_interrupt_do_not_reuse_old_input_start(self) -> None:
        self.output('user@host:~$ ')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.NoModifier, 'x'))
        self.output('x')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, '\x03'))
        self.output('^C\r\nuser@host:~$ ')
        for _ in range(3):
            self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, ' '))
            self.output(' ')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Home, Qt.NoModifier))
        self.output('\x1b[3D')
        self.emitted.clear()
        self.cleared.clear()
        task = self.fill()
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x01\x0b'])
        self.output('\r\x1b[2Kuser@host:~$ ')
        await asyncio.wait_for(task, 1)

    async def test_tab_spacing_without_pending_text_is_deleted(self) -> None:
        self.output('user@host:~$ ')
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier))
        self.output('    ')
        self.assertTrue(self.terminal.prepare_command_replacement())
        self.assertEqual(self.emitted, [b'\t', b'\x01\x0b'])

    async def test_real_text_after_cursor_is_not_mistaken_for_whitespace(self) -> None:
        self.output('user@host:~$ ')
        self.terminal._append_pending_command_text('   ')
        self.output('   recalled command$\r\x1b[16C')
        self.assertTrue(self.terminal.prepare_command_replacement())
        self.assertEqual(self.emitted, [b'\x03'])

    async def test_real_text_on_wrapped_row_after_cursor_is_not_deleted(self) -> None:
        self.output('user@host:~$ ')
        command = ' ' * (int(self.terminal.screen.columns) + 10) + 'real'
        self.terminal._append_pending_command_text(command)
        self.output(command)
        self.output('\x1b[1A\r\x1b[13C')
        self.assertTrue(self.terminal.prepare_command_replacement())
        self.assertEqual(self.emitted, [b'\x03'])

    async def test_blank_continuation_still_cancels_the_whole_command(self) -> None:
        self.output('user@host:~$ echo "unfinished\r\n> ')
        self.terminal._append_pending_command_text('   ')
        self.output('   ')
        self.assertTrue(self.terminal.prepare_command_replacement())
        self.assertEqual(self.emitted, [b'\x03'])

    async def test_wrapped_single_line_is_cancelled_before_filling(self) -> None:
        self.output('user@host:~$ ')
        command = 'echo ' + 'x' * 180
        self.terminal._append_pending_command_text(command)
        self.output(command)
        task = self.fill('pwd')
        await self.wait_clear()
        self.assertEqual(self.emitted, [b'\x03'])
        self.output('\r\nuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted[-1], b'pwd')

    async def test_typing_while_waiting_cancels_fill(self) -> None:
        self.output('user@host:~$ old')
        task = self.fill()
        await self.wait_clear()
        self.terminal.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.NoModifier, 'x'))
        await asyncio.gather(task, return_exceptions=True)
        self.output('\r\x1b[2Kuser@host:~$ x')
        self.assertTrue(task.cancelled())
        self.assertEqual(self.emitted, [b'\x03', b'x'])

    async def test_repeated_clicks_keep_only_latest_command(self) -> None:
        self.output('user@host:~$ old')
        first = self.fill('first')
        await self.wait_clear()
        self.cleared.clear()
        latest = self.fill('latest')
        await self.wait_clear()
        self.output('\r\x1b[2Kuser@host:~$ ')
        await asyncio.gather(first, latest, return_exceptions=True)
        self.assertTrue(first.cancelled())
        self.assertEqual(self.emitted, [b'\x03', b'\x03', b'latest'])
        self.assertEqual(self.window._command_fill_tasks, {})

    async def test_switching_tabs_does_not_send_to_another_terminal(self) -> None:
        self.output('user@host:~$ old')
        task = self.fill()
        await self.wait_clear()
        other = TerminalVTWidget()
        other_input = []
        other.input_received.connect(other_input.append)
        self.terminals['tab-b'] = other
        self.window._active_tab_id = 'tab-b'
        self.output('\r\x1b[2Kuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted[-1], b'git status')
        self.assertEqual(other_input, [])

    async def test_reconnection_does_not_receive_stale_command(self) -> None:
        self.output('user@host:~$ old')
        task = self.fill()
        await self.wait_clear()
        self.sessions['tab-a'] = SSHSession('tab-a')
        self.output('\r\x1b[2Kuser@host:~$ ')
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.emitted, [b'\x03'])

    async def test_timeout_does_not_send_command(self) -> None:
        self.output('user@host:~$ old')
        with (
            patch.object(self.ssh, 'wait_for_shell_ready', new=AsyncMock(return_value=False)),
            patch('ui.main_window.message_warning_async', new=AsyncMock()) as warning,
        ):
            await asyncio.wait_for(self.fill(), 1)
        warning.assert_awaited_once()
        self.assertEqual(self.emitted, [b'\x03'])

    async def test_alt_screen_and_disconnected_terminal_are_ignored(self) -> None:
        self.terminal._in_alt_screen = True
        self.window._replace_command_in_active_terminal('pwd')
        self.terminal._in_alt_screen = False
        self.terminal.set_reconnect_enabled(True)
        self.window._replace_command_in_active_terminal('pwd')
        self.assertEqual(self.window._command_fill_tasks, {})
        self.assertEqual(self.emitted, [])

    async def test_both_sidebar_double_clicks_use_replacement(self) -> None:
        command = CommandItem(name='Status', command='git status')
        panel = SidePanel(
            Mock(load=Mock(return_value=[])), Mock(),
            command_store=Mock(load=Mock(return_value=[command])),
        )
        try:
            replaced = []
            appended = []
            panel.command_replace_requested.connect(replaced.append)
            panel.command_send_requested.connect(lambda text, execute: appended.append((text, execute)))
            panel.commands_tree.itemDoubleClicked.emit(panel.commands_tree.topLevelItem(0), 0)
            panel.history_list.set_active_tab('tab-a')
            panel.history_list.add_command('tab-a', 'pwd', '10:00', 1)
            panel.history_list.itemDoubleClicked.emit(panel.history_list.item(0))
            self.assertEqual(replaced, ['git status', 'pwd'])
            self.assertEqual(appended, [])
        finally:
            panel.deleteLater()
