#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandItem:
    """A node in the quick-command tree."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ''
    command: str = ''
    description: str = ''
    children: list['CommandItem'] = field(default_factory=list)

    def is_folder(self) -> bool:
        return not self.command

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            'id': self.id,
            'name': self.name,
        }
        if self.command:
            data['command'] = self.command
            if self.description:
                data['description'] = self.description
        if self.children:
            data['children'] = [child.to_dict() for child in self.children]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'CommandItem':
        return cls(
            id=str(data.get('id', '') or uuid.uuid4()),
            name=str(data.get('name', '') or ''),
            command=str(data.get('command', '') or ''),
            description=str(data.get('description', '') or ''),
            children=[cls.from_dict(c) for c in data.get('children', []) if isinstance(c, dict)],
        )
