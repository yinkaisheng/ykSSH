#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from models.command_item import CommandItem
from models.session_item import SessionItem
from storage.command_store import CommandStore
from storage.credential_store import CredentialStore
from storage.session_profile_store import SessionProfileStore
from storage import app_config


class StorageFailureTests(unittest.TestCase):
    def test_save_app_preferences_persists_ui_font_size(self) -> None:
        raw = app_config._normalize_config(app_config._default_config())
        config = app_config._to_app_config(raw)
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            app_config,
            '_raw_config_cache',
            raw,
        ), patch.object(app_config, '_config_cache', config):
            path = Path(temp_dir) / 'config.json'

            saved = app_config.save_app_preferences(
                theme='dark',
                ui_font_size_px=19,
                terminal_font_family='Consolas',
                terminal_font_size_px=22,
                language='en',
                editor_path='',
                remote_large_file_mb=20,
                path=path,
            )

            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(data['appearance']['ui_font_size_px'], 19)
            self.assertEqual(saved.appearance.ui_font_size_px, 19)

    def test_session_save_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SessionProfileStore(Path(temp_dir) / 'sessions.json')
            with patch('storage.session_profile_store.atomic_write_json', side_effect=OSError('full')):
                self.assertFalse(store.save_items([SessionItem(name='folder')]))

    def test_command_save_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CommandStore(Path(temp_dir) / 'commands.json')
            with patch('storage.command_store.atomic_write_json', side_effect=OSError('full')):
                self.assertFalse(store.save_items([CommandItem(name='folder')]))

    def test_credential_save_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = CredentialStore(
                Path(temp_dir) / 'credentials.json',
                fernet=Fernet(Fernet.generate_key()),
            )
            with patch('storage.credential_store.atomic_write_json', side_effect=OSError('full')):
                self.assertFalse(store.set_password('session-id', 'secret'))
            self.assertIsNone(store.get_password('session-id'))


if __name__ == '__main__':
    unittest.main()
