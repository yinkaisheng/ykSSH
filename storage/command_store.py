#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from log_util import logger
from models.command_item import CommandItem
from storage.json_io import atomic_write_json
from storage.paths import COMMANDS_FILE

COMMANDS_VERSION = 1


class CommandStore:
    """Persist quick-command groups and command leaves."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else COMMANDS_FILE
        self._cache: Optional[List[CommandItem]] = None

    def load(self) -> List[CommandItem]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = []
            return self._cache
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning(f'Failed to load commands from {self.path}')
            self._cache = []
            return self._cache

        if not isinstance(data, dict) or data.get('version') != COMMANDS_VERSION:
            self._cache = []
            return self._cache
        items_data = data.get('items', [])
        self._cache = [CommandItem.from_dict(d) for d in items_data if isinstance(d, dict)]
        return self._cache

    def save_items(self, items: List[CommandItem]) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            'version': COMMANDS_VERSION,
            'items': [item.to_dict() for item in items],
        }
        try:
            atomic_write_json(self.path, payload)
            self._cache = items
            return True
        except OSError as exc:
            logger.warning(f'Failed to save commands to {self.path}: {exc}')
            return False
