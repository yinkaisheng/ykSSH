#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Favorite path entry (path + optional note) for file panel bookmarks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class FavoritePath:
    path: str
    note: str = ''
    is_file: bool | None = None

    def display_text(self) -> str:
        path = self.path.strip()
        note = self.note.strip()
        if note:
            return f'{path} ({note})'
        return path

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {'path': self.path}
        if self.note.strip():
            data['note'] = self.note.strip()
        if self.is_file is not None:
            data['is_file'] = bool(self.is_file)
        return data

    @classmethod
    def from_dict(cls, data: Any) -> FavoritePath | None:
        if not isinstance(data, dict):
            return None
        path = str(data.get('path', '') or '').strip()
        if not path:
            return None
        note = str(data.get('note', '') or '').strip()
        raw_is_file = data.get('is_file')
        is_file = raw_is_file if isinstance(raw_is_file, bool) else None
        return cls(path=path, note=note, is_file=is_file)


def favorite_paths_from_raw(value: Any) -> list[FavoritePath]:
    if not isinstance(value, list):
        return []
    entries: list[FavoritePath] = []
    for item in value:
        entry = FavoritePath.from_dict(item)
        if entry is not None:
            entries.append(entry)
    return entries


def favorite_paths_to_raw(entries: Sequence[FavoritePath]) -> list[dict[str, Any]]:
    return [entry.to_dict() for entry in entries if entry.path.strip()]
