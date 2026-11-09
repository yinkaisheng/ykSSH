# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication, QWidget

from storage.command_store import CommandStore
from ui.command_dialog import CommandDialog


class CommandDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_multiline_command_survives_save_reopen_and_resave(self) -> None:
        commands = [
            'ls\npwd',
            'cat <<\'EOF\'\n<b>literal</b> &amp;\n\n  indented\nEOF',
            '  echo first\n\n\techo second\n\n',
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'commands.json'
            for text in commands:
                with self.subTest(text=text):
                    parent = QWidget()
                    try:
                        dialog = CommandDialog(parent, title='test')
                        dialog.name_edit.setText('example')
                        dialog.command_edit.setPlainText(text)
                        saved = dialog.get_command()
                        self.assertIsNotNone(saved)
                        self.assertTrue(CommandStore(path).save_items([saved]))
                        loaded = CommandStore(path).load()[0]
                        reopened = CommandDialog(parent, title='test', command=loaded)
                        self.assertEqual(reopened.command_edit.toPlainText(), text)
                        updated = reopened.get_command(existing=loaded)
                        self.assertIs(updated, loaded)
                        self.assertTrue(CommandStore(path).save_items([updated]))
                        self.assertEqual(CommandStore(path).load()[0].command, text)
                    finally:
                        parent.deleteLater()
                        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_multiline_description_survives_save_reopen_and_resave(self) -> None:
        description = 'first\n\n  second\n<b>literal</b> &amp;'
        parent = QWidget()
        try:
            dialog = CommandDialog(parent, title='test')
            dialog.name_edit.setText('example')
            dialog.command_edit.setPlainText('pwd')
            dialog.description_edit.setPlainText(description)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'commands.json'
                self.assertTrue(CommandStore(path).save_items([dialog.get_command()]))
                loaded = CommandStore(path).load()[0]
                reopened = CommandDialog(parent, title='test', command=loaded)
                self.assertEqual(reopened.description_edit.toPlainText(), description)
                updated = reopened.get_command(existing=loaded)
                self.assertTrue(CommandStore(path).save_items([updated]))
                self.assertEqual(CommandStore(path).load()[0].description, description)
        finally:
            parent.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
