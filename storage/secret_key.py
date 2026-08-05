#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local Fernet key for encrypting credentials (stored under config/)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

from log_util import logger
from storage.paths import SECRET_KEY_FILE


class InvalidSecretKeyError(RuntimeError):
    """Raised when an existing secret.key cannot be loaded as a Fernet key."""


def load_or_create_fernet(path: Optional[Path] = None) -> Fernet:
    key_path = Path(path) if path is not None else SECRET_KEY_FILE
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        try:
            key = key_path.read_bytes().strip()
            return Fernet(key)
        except Exception as exc:
            logger.error(f'Invalid secret key at {key_path}: {exc}')
            raise InvalidSecretKeyError(
                f'Invalid secret key at {key_path}. '
                'Restore a matching secret.key with credentials.json, '
                'or remove both files to start fresh.'
            ) from exc
    key = Fernet.generate_key()
    try:
        key_path.write_bytes(key)
    except OSError as exc:
        logger.warning(f'Failed to write secret key to {key_path}: {exc}')
    return Fernet(key)
