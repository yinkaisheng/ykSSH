#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Favorite path entry (path + optional note) for file panel bookmarks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence


@dataclass
class FavoritePath:
    path: str
    note: str = ''

    def display_text(self) -> str:
        path = self.path.strip()
        note = self.note.strip()
        if note:
            return f'{path} ({note})'
        return path

    def to_dict(self) -> Dict[str, str]:
        data = {'path': self.path}
        if self.note.strip():
            data['note'] = self.note.strip()
        return data

    @classmethod
    def from_dict(cls, data: Any) -> FavoritePath | None:
        if isinstance(data, str):
            path = data.strip()
            if not path:
                return None
            return cls(path=path)
        if not isinstance(data, dict):
            return None
        path = str(data.get('path', '') or '').strip()
        if not path:
            return None
        note = str(data.get('note', '') or '').strip()
        return cls(path=path, note=note)


def favorite_paths_from_raw(value: Any) -> List[FavoritePath]:
    if not isinstance(value, list):
        return []
    entries: List[FavoritePath] = []
    for item in value:
        entry = FavoritePath.from_dict(item)
        if entry is not None:
            entries.append(entry)
    return entries


def favorite_paths_to_raw(entries: Sequence[FavoritePath]) -> List[Dict[str, str]]:
    return [entry.to_dict() for entry in entries if entry.path.strip()]
