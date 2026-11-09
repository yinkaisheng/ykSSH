#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QWidget

from ui.line_number_edit import LineNumberEdit
from ui.prompt_dialog import prompt_multiline_confirm
from ui.terminal_vt_widget import TerminalVTWidget, _paste_text_without_trailing_blank_lines


class PasteTextHelperTests(unittest.TestCase):
    def test_strips_consecutive_trailing_blank_lines(self) -> None:
        self.assertEqual(_paste_text_without_trailing_blank_lines('hostnamectl\n\n'), 'hostnamectl')
        self.assertEqual(_paste_text_without_trailing_blank_lines('hostnamectl\r\n\r\n'), 'hostnamectl')
        self.assertEqual(_paste_text_without_trailing_blank_lines('hostnamectl\n  \n\t\n'), 'hostnamectl')
        self.assertEqual(_paste_text_without_trailing_blank_lines('ls\npwd\n\n'), 'ls\npwd')
        self.assertEqual(_paste_text_without_trailing_blank_lines('\n\n'), '')


class TerminalPasteConfirmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.widget = TerminalVTWidget()
        self.emitted: list[bytes] = []
        self.widget.input_received.connect(self.emitted.append)

    def _settings(self, **overrides: object):
        values = {
            'terminal_paste_confirm_multiline': True,
            'terminal_bracketed_paste': False,
        }
        values.update(overrides)

        def fake_get_setting(key, default=None):
            return values.get(key, default)

        return patch('ui.terminal_vt_widget.get_setting', side_effect=fake_get_setting)

    def test_single_command_with_trailing_blank_lines_pastes_directly(self) -> None:
        with (
            self._settings(),
            patch('ui.terminal_vt_widget.prompt_multiline_confirm') as confirm,
        ):
            self.widget._send_paste_text('hostnamectl\n\n')

        confirm.assert_not_called()
        self.assertEqual(self.emitted, [b'hostnamectl'])

    def test_real_multiline_paste_still_confirms(self) -> None:
        with (
            self._settings(),
            patch(
                'ui.terminal_vt_widget.prompt_multiline_confirm',
                return_value='ls\npwd\n\n',
            ) as confirm,
        ):
            self.widget._send_paste_text('ls\npwd\n\n')

        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.args[3], 'ls\npwd\n\n')
        self.assertEqual(self.emitted, [b'ls\rpwd\r\r'])

    def test_blank_clipboard_does_not_paste(self) -> None:
        with (
            self._settings(),
            patch('ui.terminal_vt_widget.prompt_multiline_confirm') as confirm,
        ):
            self.widget._send_paste_text('\n\n  \n')

        confirm.assert_not_called()
        self.assertEqual(self.emitted, [])


class MultilinePromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_numbered_editor_returns_edits_or_cancels(self) -> None:
        initial = 'ls\n\npwd\n'
        edited = '  echo <literal>\n\tprintf second\n\n'
        for accept in (True, False):
            with self.subTest(accept=accept):
                parent = QWidget()
                observed = []

                def respond() -> None:
                    dialog = parent.findChild(QDialog)
                    editor = dialog.findChild(LineNumberEdit)
                    if editor is not None:
                        observed.append(editor.toPlainText())
                        editor.setPlainText(edited)
                    buttons = dialog.findChild(QDialogButtonBox)
                    buttons.button(QDialogButtonBox.Yes if accept else QDialogButtonBox.No).click()

                try:
                    QTimer.singleShot(0, respond)
                    result = prompt_multiline_confirm(parent, 'test', 'test', initial)
                    self.assertEqual(observed, [initial])
                    self.assertEqual(result, edited if accept else None)
                finally:
                    parent.deleteLater()


if __name__ == '__main__':
    unittest.main()
