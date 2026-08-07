#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

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


class StorageFailureTests(unittest.TestCase):
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
