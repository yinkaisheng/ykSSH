#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portable encrypted session password storage under config/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from log_util import logger
from storage.json_io import atomic_write_json
from storage.paths import CREDENTIALS_FILE
from storage.secret_key import load_or_create_fernet

_CREDENTIALS_VERSION = 1


class CredentialStore:
    """Persist session passwords in config/credentials.json (Fernet-encrypted)."""

    def __init__(self, path: Optional[Path] = None, fernet: Optional[Fernet] = None) -> None:
        self.path = Path(path) if path is not None else CREDENTIALS_FILE
        self._fernet = fernet or load_or_create_fernet()
        self._passwords: Dict[str, str] = {}
        self._save_blocked = False
        self._load()

    def _encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode('utf-8')).decode('ascii')

    def _decrypt(self, token: str) -> Optional[str]:
        try:
            return self._fernet.decrypt(token.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError, UnicodeDecodeError):
            return None

    def _load(self) -> None:
        if not self.path.exists():
            self._passwords = {}
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f'Failed to load credentials from {self.path}: {exc}')
            self._passwords = {}
            return
        if not isinstance(data, dict):
            logger.warning(f'Credentials file is not an object: {self.path}')
            self._passwords = {}
            return
        if data.get('version') != _CREDENTIALS_VERSION:
            logger.warning(
                f'Unsupported credentials version at {self.path}: '
                f'{data.get("version")!r} (expected {_CREDENTIALS_VERSION})'
            )
            self._passwords = {}
            self._save_blocked = True
            return
        raw = data.get('passwords', {})
        if not isinstance(raw, dict):
            logger.warning(f'Credentials passwords map invalid: {self.path}')
            self._passwords = {}
            return

        passwords: Dict[str, str] = {}
        decrypt_failures = 0
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            decrypted = self._decrypt(value)
            if decrypted is not None:
                passwords[key] = decrypted
            else:
                decrypt_failures += 1
        if decrypt_failures:
            logger.warning(
                f'Failed to decrypt {decrypt_failures} credential entries in {self.path}'
            )
        self._passwords = passwords

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': _CREDENTIALS_VERSION,
            'passwords': {
                session_id: self._encrypt(password)
                for session_id, password in self._passwords.items()
            },
        }
        try:
            if self._save_blocked:
                logger.warning(
                    f'Skipped saving credentials because {self.path} was loaded from an '
                    'unsupported version'
                )
                return
            atomic_write_json(self.path, payload)
        except OSError as exc:
            logger.warning(f'Failed to save credentials to {self.path}: {exc}')

    def get_password(self, session_id: str) -> Optional[str]:
        return self._passwords.get(session_id)

    def set_password(self, session_id: str, password: str) -> None:
        self._passwords[session_id] = password
        self._save()

    def delete_password(self, session_id: str) -> None:
        if session_id in self._passwords:
            del self._passwords[session_id]
            self._save()
