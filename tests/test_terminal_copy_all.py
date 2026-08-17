#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pyte  # type: ignore
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QApplication

from ui.terminal_vt_widget import TerminalVTWidget


class TerminalCopyAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = TerminalVTWidget()
        screen = pyte.HistoryScreen(10, 3, history=20)  # type: ignore[attr-defined]
        stream = pyte.Stream(screen)  # type: ignore[attr-defined]
        stream.feed('one\r\ntwo\r\nthree\r\nfour\r\nfive')
        screen.prev_page()
        self.widget._main_screen = screen
        self.widget.screen = screen
        self.widget._in_alt_screen = False
        QGuiApplication.clipboard().clear()

    def test_context_menu_copy_all_includes_scrollback_and_visible_content(self) -> None:
        def trigger_copy_all(menu, _global_pos):
            menu.actions()[1].trigger()
            return menu.actions()[1]

        event = SimpleNamespace(globalPos=lambda: QPoint(0, 0))
        with patch('ui.terminal_vt_widget.exec_menu', side_effect=trigger_copy_all):
            self.widget.contextMenuEvent(event)

        self.assertEqual(
            QGuiApplication.clipboard().text(),
            'one\ntwo\nthree\nfour\nfive',
        )

    def test_context_menu_shortcuts_use_copy_all_a_and_select_all_s(self) -> None:
        shortcuts: dict[int, object] = {}

        def capture_shortcuts(menu, _global_pos):
            shortcuts.update(menu._key_actions)
            return None

        event = SimpleNamespace(globalPos=lambda: QPoint(0, 0))
        with patch('ui.terminal_vt_widget.exec_menu', side_effect=capture_shortcuts):
            self.widget.contextMenuEvent(event)

        self.assertEqual(shortcuts[Qt.Key_A].text().split('\t')[0], 'Copy All')
        self.assertEqual(shortcuts[Qt.Key_S].text().split('\t')[0], 'Select All')

    def test_change_directory_clears_input_and_executes_quoted_cd(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)

        self.widget.change_directory("/srv/hello world's files")

        self.assertEqual(
            emitted,
            [b'\x01\x0b', b'cd -- \'/srv/hello world\'"\'"\'s files\'\r'],
        )

    def test_change_directory_preserves_spaces_and_rejects_control_characters(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)

        self.widget.change_directory('/srv/trailing ')
        self.widget.change_directory('/srv/unsafe\nwhoami')

        self.assertEqual(
            emitted,
            [b'\x01\x0b', b"cd -- '/srv/trailing '\r"],
        )

    def test_change_directory_does_not_reuse_previous_command_start(self) -> None:
        screen = pyte.HistoryScreen(20, 3, history=20)  # type: ignore[attr-defined]
        pyte.Stream(screen).feed('$ abcdef')  # type: ignore[attr-defined]
        screen.cursor.x = 4
        self.widget._main_screen = screen
        self.widget.screen = screen
        submitted: list[tuple[str, int]] = []
        self.widget.command_submitted.connect(
            lambda command, _sent_at, row: submitted.append((command, row))
        )
        self.widget._pending_command_start_y = 7
        self.widget._pending_command_start_x = 4
        self.widget._pending_command_text = 'old input'

        self.widget.change_directory('/srv/project')

        self.assertNotIn(7, self.widget._command_start_rows)
        self.assertNotIn(7, [row for _command, row in submitted])
        self.assertEqual([command for command, _row in submitted], ['cd -- /srv/project'])

    def test_working_directory_report_handles_split_osc_without_displaying_it(self) -> None:
        screen = pyte.HistoryScreen(40, 4, history=20)  # type: ignore[attr-defined]
        stream = pyte.Stream(screen)  # type: ignore[attr-defined]
        self.widget._main_screen = screen
        self.widget._main_stream = stream
        self.widget.screen = screen
        self.widget.stream = stream
        paths: list[str] = []
        self.widget.working_directory_reported.connect(paths.append)

        self.widget.write_text('before\r\n\x1b]777;ykssh-')
        self.widget.write_text('cwd;/srv/hello world\x07after')

        self.assertEqual(paths, [])
        visible = '\n'.join(
            self.widget._line_text(row)
            for row in range(int(getattr(self.widget.screen, 'lines', 0)))
        )
        self.assertIn('before', visible)
        self.assertIn('after', visible)
        self.assertNotIn('ykssh-cwd', visible)

        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)
        self.widget.request_working_directory()

        self.assertEqual(paths, ['/srv/hello world'])
        self.assertEqual(emitted, [])

    def test_standard_osc_7_is_cached_decoded_and_removed_from_output(self) -> None:
        screen = pyte.HistoryScreen(50, 4, history=20)  # type: ignore[attr-defined]
        stream = pyte.Stream(screen)  # type: ignore[attr-defined]
        self.widget._main_screen = screen
        self.widget._main_stream = stream
        self.widget.screen = screen
        self.widget.stream = stream
        paths: list[str] = []
        emitted: list[bytes] = []
        self.widget.working_directory_reported.connect(paths.append)
        self.widget.input_received.connect(emitted.append)

        self.widget.write_text('before\x1b]7;file://remote/srv/hello%20')
        self.widget.write_text('world\x1b\\after')
        self.widget.request_working_directory()

        self.assertEqual(paths, ['/srv/hello world'])
        self.assertEqual(emitted, [])
        visible = '\n'.join(
            self.widget._line_text(row)
            for row in range(int(getattr(self.widget.screen, 'lines', 0)))
        )
        self.assertIn('before', visible)
        self.assertIn('after', visible)
        self.assertNotIn('file://', visible)

    def test_prompt_path_cache_avoids_terminal_input_for_multiline_command(self) -> None:
        screen = pyte.HistoryScreen(80, 4, history=20)  # type: ignore[attr-defined]
        stream = pyte.Stream(screen)  # type: ignore[attr-defined]
        stream.feed('hy@ps:/mnt/sdb/yks$ ')
        self.widget._main_screen = screen
        self.widget._main_stream = stream
        self.widget.screen = screen
        self.widget.stream = stream
        emitted: list[bytes] = []
        paths: list[str] = []
        self.widget.input_received.connect(emitted.append)
        self.widget.working_directory_reported.connect(paths.append)

        self.widget._append_pending_command_text('ls \\\n-lh')
        self.widget.request_working_directory()

        self.assertEqual(paths, ['/mnt/sdb/yks'])
        self.assertEqual(emitted, [])
        self.assertEqual(self.widget._pending_command_text, 'ls \\\n-lh')

    def test_root_prompt_path_is_recognized(self) -> None:
        self.assertEqual(
            self.widget._working_directory_from_prompt('root@host:/var/lib/app# '),
            '/var/lib/app',
        )

    def test_shell_prompt_ready_distinguishes_banner_from_prompt(self) -> None:
        screen = pyte.HistoryScreen(80, 4, history=20)  # type: ignore[attr-defined]
        stream = pyte.Stream(screen)  # type: ignore[attr-defined]
        self.widget._main_screen = screen
        self.widget._main_stream = stream
        self.widget.screen = screen
        self.widget.stream = stream

        stream.feed('Maintenance window #')
        self.assertFalse(self.widget.shell_prompt_ready())

        stream.feed('\r\nhy@st-Rack-Server:~$ ')
        self.assertTrue(self.widget.shell_prompt_ready())

    def test_alt_screen_uses_cached_path_without_active_query(self) -> None:
        emitted: list[bytes] = []
        paths: list[str] = []
        self.widget.input_received.connect(emitted.append)
        self.widget.working_directory_reported.connect(paths.append)
        self.widget._in_alt_screen = True

        self.widget.request_working_directory()
        self.assertEqual(emitted, [])
        self.assertEqual(paths, [])

        self.widget._working_directory_path = '/srv/cached'
        self.widget.request_working_directory()
        self.assertEqual(emitted, [])
        self.assertEqual(paths, ['/srv/cached'])

    def test_reconnect_state_does_not_query_working_directory(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)
        self.widget.set_reconnect_enabled(True)

        self.widget.request_working_directory()

        self.assertEqual(emitted, [])

    def test_disconnect_clears_cached_working_directory(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)
        self.widget._working_directory_path = '/srv/old-connection'

        self.widget.set_reconnect_enabled(True)
        self.widget.set_reconnect_enabled(False)
        self.widget.request_working_directory()

        self.assertEqual(
            emitted,
            [
                b"\x01\x0b printf '\\033[1A\\033[2K\\r\\033]777;ykssh-cwd;%s\\007' "
                b'"$PWD"\r',
            ],
        )

    def test_request_working_directory_restores_pending_input_after_report(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)
        self.widget._pending_command_text = 'unfinished'

        self.widget.request_working_directory()

        self.assertEqual(
            emitted,
            [
                b"\x01\x0b printf '\\033[1A\\033[2K\\r\\033]777;ykssh-cwd;%s\\007' "
                b'"$PWD"\r',
            ],
        )
        self.assertEqual(self.widget._pending_command_text, 'unfinished')

        self.widget.write_text('\x1b]777;ykssh-cwd;/srv/project\x07')

        self.assertEqual(emitted[-1], b'\x19')
        self.assertEqual(self.widget._pending_command_text, 'unfinished')

    def test_request_working_directory_does_not_yank_old_input_when_line_is_empty(self) -> None:
        emitted: list[bytes] = []
        self.widget.input_received.connect(emitted.append)

        self.widget.request_working_directory()
        self.widget.write_text('\x1b]777;ykssh-cwd;/srv/project\x07')

        self.assertEqual(len(emitted), 1)


if __name__ == '__main__':
    unittest.main()
