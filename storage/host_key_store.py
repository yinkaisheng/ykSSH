#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TOFU SSH server host-key storage under config/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

import asyncssh

from log_util import logger
from storage.json_io import atomic_write_json
from storage.paths import HOST_KEYS_FILE

HostKeyStatus = Literal['trusted', 'unknown', 'changed']
_HOST_KEYS_VERSION = 1


class HostKeyStore:
    """Persist the first trusted public key for each SSH endpoint."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else HOST_KEYS_FILE
        self._keys: dict[str, str] = {}
        self._load()

    @staticmethod
    def endpoint(host: str, port: int) -> str:
        return f'[{host.strip().lower()}]:{int(port)}'

    @staticmethod
    def export_key(key: asyncssh.SSHKey) -> str:
        return key.export_public_key().decode('ascii').strip()

    def check(self, host: str, port: int, key: asyncssh.SSHKey) -> HostKeyStatus:
        saved = self._keys.get(self.endpoint(host, port))
        if saved is None:
            return 'unknown'
        return 'trusted' if saved == self.export_key(key) else 'changed'

    def fingerprint(self, host: str, port: int) -> str:
        saved = self._keys.get(self.endpoint(host, port))
        if not saved:
            return ''
        try:
            return asyncssh.import_public_key(saved).get_fingerprint()
        except (asyncssh.KeyImportError, ValueError):
            return ''

    def has(self, host: str, port: int) -> bool:
        return self.endpoint(host, port) in self._keys

    def forget(self, host: str, port: int) -> bool:
        endpoint = self.endpoint(host, port)
        if endpoint not in self._keys:
            return True
        previous = self._keys.pop(endpoint)
        try:
            atomic_write_json(self.path, {
                'version': _HOST_KEYS_VERSION,
                'keys': dict(sorted(self._keys.items())),
            })
            return True
        except OSError as exc:
            self._keys[endpoint] = previous
            logger.warning(f'Failed to forget SSH host key at {self.path}: {exc}')
            return False

    def trust(self, host: str, port: int, key: asyncssh.SSHKey) -> None:
        endpoint = self.endpoint(host, port)
        existed = endpoint in self._keys
        previous = self._keys.get(endpoint)
        self._keys[endpoint] = self.export_key(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_json(self.path, {
                'version': _HOST_KEYS_VERSION,
                'keys': dict(sorted(self._keys.items())),
            })
        except OSError:
            if existed and previous is not None:
                self._keys[endpoint] = previous
            else:
                self._keys.pop(endpoint, None)
            raise

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f'Failed to load SSH host keys from {self.path}: {exc}')
            return
        if not isinstance(data, dict) or data.get('version') != _HOST_KEYS_VERSION:
            logger.warning(f'Unsupported SSH host key file: {self.path}')
            return
        raw = data.get('keys')
        if isinstance(raw, dict):
            self._keys = {
                key: value for key, value in raw.items()
                if isinstance(key, str) and isinstance(value, str)
            }
