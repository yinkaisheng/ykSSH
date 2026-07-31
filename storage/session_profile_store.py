#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from log_util import logger
from models.session_item import SessionItem
from storage.paths import SESSIONS_FILE


class SessionProfileStore:
    """Persist a flat list of root ``SessionItem`` nodes to sessions.json."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else SESSIONS_FILE
        self._cache: Optional[List[SessionItem]] = None

    def load(self) -> List[SessionItem]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = []
            return self._cache
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning(f'Failed to load sessions from {self.path}')
            self._cache = []
            return self._cache

        if not isinstance(data, dict):
            self._cache = []
            return self._cache

        items_data = data.get('items', [])
        self._cache = [SessionItem.from_dict(d) for d in items_data if isinstance(d, dict)]
        return self._cache

    def save_items(self, items: List[SessionItem]) -> None:
        self._ensure_dir()
        payload: Dict[str, Any] = {
            'version': 1,
            'items': [item.to_dict() for item in items],
        }
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._cache = items
        except OSError:
            logger.warning(f'Failed to save sessions to {self.path}')

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
