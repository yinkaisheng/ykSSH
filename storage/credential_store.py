#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portable encrypted session password storage under config/."""
from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from log_util import logger
from storage.json_io import atomic_write_json
from storage.paths import CREDENTIALS_FILE
from storage.secret_key import load_or_create_fernet

_CREDENTIALS_VERSION = 1


class CredentialStore:
    """Persist session passwords in config/credentials.json (Fernet-encrypted)."""

    def __init__(self, path: Path | None = None, fernet: Fernet | None = None) -> None:
        self.path = Path(path) if path is not None else CREDENTIALS_FILE
        self._fernet = fernet or load_or_create_fernet()
        self._passwords: dict[str, str] = {}
        self._hop_passwords: dict[str, dict[str, str]] = {}
        self._save_blocked = False
        self._load()

    def _encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode('utf-8')).decode('ascii')

    def _decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode('ascii')).decode('utf-8')
        except (InvalidToken, ValueError, UnicodeDecodeError):
            return None

    def _load(self) -> None:
        if not self.path.exists():
            self._passwords = {}
            self._hop_passwords = {}
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f'Failed to load credentials from {self.path}: {exc}')
            self._passwords = {}
            self._hop_passwords = {}
            return
        if not isinstance(data, dict):
            logger.warning(f'Credentials file is not an object: {self.path}')
            self._passwords = {}
            self._hop_passwords = {}
            return
        if data.get('version') != _CREDENTIALS_VERSION:
            logger.warning(
                f'Unsupported credentials version at {self.path}: '
                f'{data.get("version")!r} (expected {_CREDENTIALS_VERSION})'
            )
            self._passwords = {}
            self._hop_passwords = {}
            self._save_blocked = True
            return
        raw = data.get('passwords', {})
        if not isinstance(raw, dict):
            logger.warning(f'Credentials passwords map invalid: {self.path}')
            self._passwords = {}
            self._hop_passwords = {}
            return

        passwords: dict[str, str] = {}
        decrypt_failures = 0
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            decrypted = self._decrypt(value)
            if decrypted is not None:
                passwords[key] = decrypted
            else:
                decrypt_failures += 1
        hop_passwords, hop_failures = self._decode_hop_passwords(data.get('hop_passwords', {}))
        decrypt_failures += hop_failures
        if decrypt_failures:
            logger.warning(
                f'Failed to decrypt {decrypt_failures} credential entries in {self.path}'
            )
        self._passwords = passwords
        self._hop_passwords = hop_passwords

    def _decode_hop_passwords(self, raw: object) -> tuple[dict[str, dict[str, str]], int]:
        if raw in (None, {}):
            return {}, 0
        if not isinstance(raw, dict):
            logger.warning(f'Credentials hop_passwords map invalid: {self.path}')
            return {}, 0
        hop_passwords: dict[str, dict[str, str]] = {}
        decrypt_failures = 0
        for session_id, mapping in raw.items():
            if not isinstance(session_id, str) or not isinstance(mapping, dict):
                continue
            decoded: dict[str, str] = {}
            for alias, value in mapping.items():
                if not isinstance(alias, str) or not isinstance(value, str):
                    continue
                decrypted = self._decrypt(value)
                if decrypted is None:
                    decrypt_failures += 1
                    continue
                decoded[alias] = decrypted
            if decoded:
                hop_passwords[session_id] = decoded
        return hop_passwords, decrypt_failures

    def _save(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': _CREDENTIALS_VERSION,
            'passwords': {
                session_id: self._encrypt(password)
                for session_id, password in self._passwords.items()
            },
            'hop_passwords': {
                session_id: {
                    alias: self._encrypt(password)
                    for alias, password in mapping.items()
                }
                for session_id, mapping in self._hop_passwords.items()
            },
        }
        try:
            if self._save_blocked:
                logger.warning(
                    f'Skipped saving credentials because {self.path} was loaded from an '
                    'unsupported version'
                )
                return False
            atomic_write_json(self.path, payload)
            return True
        except OSError as exc:
            logger.warning(f'Failed to save credentials to {self.path}: {exc}')
            return False

    def get_password(self, session_id: str) -> str | None:
        return self._passwords.get(session_id)

    def set_password(self, session_id: str, password: str) -> bool:
        existed = session_id in self._passwords
        previous = self._passwords.get(session_id)
        self._passwords[session_id] = password
        if self._save():
            return True
        if existed and previous is not None:
            self._passwords[session_id] = previous
        else:
            self._passwords.pop(session_id, None)
        return False

    def delete_password(self, session_id: str) -> bool:
        if session_id in self._passwords:
            previous = self._passwords.pop(session_id)
            if self._save():
                return True
            self._passwords[session_id] = previous
            return False
        return True

    def get_hop_passwords(self, session_id: str) -> dict[str, str]:
        return dict(self._hop_passwords.get(session_id, {}))

    def set_hop_passwords(self, session_id: str, passwords: dict[str, str]) -> bool:
        existed = session_id in self._hop_passwords
        previous = dict(self._hop_passwords.get(session_id, {}))
        cleaned = {
            alias: value
            for alias, value in passwords.items()
            if alias and value
        }
        if cleaned:
            self._hop_passwords[session_id] = cleaned
        else:
            self._hop_passwords.pop(session_id, None)
        if self._save():
            return True
        if existed:
            self._hop_passwords[session_id] = previous
        else:
            self._hop_passwords.pop(session_id, None)
        return False

    def delete_hop_passwords(self, session_id: str) -> bool:
        if session_id not in self._hop_passwords:
            return True
        previous = self._hop_passwords.pop(session_id)
        if self._save():
            return True
        self._hop_passwords[session_id] = previous
        return False

    def delete_session_credentials(self, session_id: str) -> bool:
        had_password = session_id in self._passwords
        had_hops = session_id in self._hop_passwords
        previous_password = self._passwords.pop(session_id, None)
        previous_hops = self._hop_passwords.pop(session_id, None)
        if not had_password and not had_hops:
            return True
        if self._save():
            return True
        if had_password and previous_password is not None:
            self._passwords[session_id] = previous_password
        if had_hops and previous_hops is not None:
            self._hop_passwords[session_id] = previous_hops
        return False
