#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from models.session_item import AUTH_PASSWORD
from ui.session_dialog import CONNECT_DIRECT, CONNECT_SSH_CONFIG, SessionDialog


class SessionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_config_encoding_error_allows_loading_another_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'config'
            path.write_bytes(b'# invalid UTF-8: \xff\nHost dest\n')
            dialog = SessionDialog()
            try:
                dialog.connect_mode_combo.setCurrentIndex(
                    dialog.connect_mode_combo.findData(CONNECT_SSH_CONFIG)
                )
                dialog.ssh_config_path_edit.setText(str(path))
                dialog._reload_ssh_config_hosts()
                self.assertTrue(dialog._config_error)
                self.assertEqual(dialog.ssh_config_preview.text(), dialog._config_error)
                self.assertFalse(dialog.ssh_config_host_combo.signalsBlocked())
                self.assertEqual(dialog.ssh_config_host_combo.count(), 0)

                path.write_text('Host dest\n HostName 192.0.2.20\n User user\n', encoding='utf-8')
                dialog._reload_ssh_config_hosts()
                self.assertEqual(dialog._config_error, '')
                self.assertEqual(dialog.ssh_config_host_combo.currentData(), 'dest')
                self.assertIn('192.0.2.20', dialog.ssh_config_preview.text())
                self.assertFalse(dialog.ssh_config_host_combo.signalsBlocked())
            finally:
                dialog.deleteLater()
                self.app.processEvents()

    def test_config_mode_shows_password_field_per_hop(self) -> None:
        text = """
Host jump-01
    HostName 192.0.2.10
    User jumpuser
Host inner-01
    HostName 10.0.0.8
    User inneruser
    ProxyJump jump-01
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'jumpconfig'
            path.write_text(text, encoding='utf-8')
            dialog = SessionDialog(
                initial_hop_passwords={'jump-01': 'jump-secret'},
            )
            try:
                dialog.connect_mode_combo.setCurrentIndex(
                    dialog.connect_mode_combo.findData(CONNECT_SSH_CONFIG)
                )
                dialog.ssh_config_path_edit.setText(str(path))
                dialog._reload_ssh_config_hosts('inner-01')
                self.assertIn('192.0.2.10', dialog.ssh_config_preview.text())
                self.assertIn('10.0.0.8', dialog.ssh_config_preview.text())
                self.assertIn('jump-01', dialog.ssh_config_preview.text())
                self.assertEqual(set(dialog._hop_password_edits), {'jump-01', 'inner-01'})
                self.assertEqual(dialog._hop_password_edits['jump-01'].text(), 'jump-secret')
                self.assertEqual(dialog._hop_password_edits['inner-01'].text(), '')
                self.assertFalse(dialog.password_edit.isVisible())
                dialog.name_edit.setText('inner')
                dialog._hop_password_edits['inner-01'].setText('inner-secret')
                dialog._on_accept()
                self.assertEqual(
                    dialog.get_hop_passwords(),
                    {'jump-01': 'jump-secret', 'inner-01': 'inner-secret'},
                )
                self.assertEqual(dialog.get_password(), '')
                session = dialog.get_session()
                self.assertEqual(session.auth_type, AUTH_PASSWORD)
                self.assertTrue(session.uses_ssh_config())
            finally:
                dialog.deleteLater()
                self.app.processEvents()

    def test_config_mode_only_asks_password_for_hops_without_identity_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / 'id_ed25519'
            key_path.write_text('dummy-key\n', encoding='utf-8')
            path = Path(temp_dir) / 'jumpconfig'
            path.write_text(
                f"""
Host jump-01
    HostName 192.0.2.10
    User jumpuser
    IdentityFile {key_path}
Host inner-01
    HostName 10.0.0.8
    User inneruser
    ProxyJump jump-01
""",
                encoding='utf-8',
            )
            dialog = SessionDialog(initial_hop_passwords={'inner-01': 'inner-secret'})
            try:
                dialog.connect_mode_combo.setCurrentIndex(
                    dialog.connect_mode_combo.findData(CONNECT_SSH_CONFIG)
                )
                dialog.ssh_config_path_edit.setText(str(path))
                dialog._reload_ssh_config_hosts('inner-01')
                self.assertEqual(set(dialog._hop_password_edits), {'inner-01'})
                self.assertEqual(dialog._hop_password_edits['inner-01'].text(), 'inner-secret')
                dialog.name_edit.setText('inner')
                dialog._on_accept()
                self.assertEqual(dialog.get_hop_passwords(), {'inner-01': 'inner-secret'})
            finally:
                dialog.deleteLater()
                self.app.processEvents()

    @staticmethod
    def _vertical_gap(upper, lower) -> int:
        window = upper.window()
        upper_bottom = upper.mapTo(window, upper.rect().bottomLeft()).y()
        lower_top = lower.mapTo(window, lower.rect().topLeft()).y()
        return lower_top - upper_bottom

    def test_hidden_connect_mode_rows_do_not_leave_vertical_gap(self) -> None:
        dialog = SessionDialog()
        try:
            dialog.show()
            dialog.adjustSize()
            self.app.processEvents()
            self.assertTrue(dialog.direct_fields.isVisible())
            self.assertFalse(dialog.config_fields.isVisible())
            self.assertLess(self._vertical_gap(dialog.password_edit, dialog.local_path_edit), 30)

            dialog.connect_mode_combo.setCurrentIndex(
                dialog.connect_mode_combo.findData(CONNECT_SSH_CONFIG)
            )
            dialog.adjustSize()
            self.app.processEvents()
            self.assertFalse(dialog.direct_fields.isVisible())
            self.assertTrue(dialog.config_fields.isVisible())
            self.assertLess(
                self._vertical_gap(dialog.connect_mode_combo, dialog.ssh_config_path_edit),
                30,
            )
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
