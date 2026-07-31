#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portable encrypted session password storage under config/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from log_util import logger
from storage.paths import CREDENTIALS_FILE
from storage.secret_key import load_or_create_fernet

_LEGACY_KEYRING_SERVICE = 'MyPyShell'
_CREDENTIALS_VERSION = 2


class CredentialStore:
    """Persist session passwords in config/credentials.json (Fernet-encrypted)."""

    def __init__(self, path: Optional[Path] = None, fernet: Optional[Fernet] = None) -> None:
        self.path = Path(path) if path is not None else CREDENTIALS_FILE
        self._fernet = fernet or load_or_create_fernet()
        self._passwords: Dict[str, str] = {}
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
        except (json.JSONDecodeError, OSError):
            logger.warning(f'Failed to load credentials from {self.path}')
            self._passwords = {}
            return
        if not isinstance(data, dict):
            self._passwords = {}
            return
        raw = data.get('passwords', {})
        if not isinstance(raw, dict):
            self._passwords = {}
            return

        dirty = False
        passwords: Dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            decrypted = self._decrypt(value)
            if decrypted is not None:
                passwords[key] = decrypted
            else:
                # Legacy plaintext (version 1) — keep and re-encrypt on save.
                passwords[key] = value
                dirty = True
        self._passwords = passwords
        if dirty or int(data.get('version', 1)) < _CREDENTIALS_VERSION:
            self._save()

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
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.warning(f'Failed to save credentials to {self.path}')

    def _migrate_from_keyring(self, session_id: str) -> Optional[str]:
        try:
            import keyring
        except ImportError:
            return None
        try:
            legacy = keyring.get_password(_LEGACY_KEYRING_SERVICE, session_id)
        except Exception as exc:
            logger.warning(f'Legacy keyring read failed: {exc}')
            return None
        if legacy:
            self._passwords[session_id] = legacy
            self._save()
        return legacy

    def get_password(self, session_id: str) -> Optional[str]:
        pwd = self._passwords.get(session_id)
        if pwd:
            return pwd
        return self._migrate_from_keyring(session_id)

    def set_password(self, session_id: str, password: str) -> None:
        self._passwords[session_id] = password
        self._save()

    def delete_password(self, session_id: str) -> None:
        if session_id in self._passwords:
            del self._passwords[session_id]
            self._save()
