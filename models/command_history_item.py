#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class CommandHistoryItem:
    """A command line sent to a terminal."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    command: str = ''
    sent_at: str = ''
    command_start_row: int = -1

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'command': self.command,
            'sent_at': self.sent_at,
            'command_start_row': self.command_start_row,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CommandHistoryItem':
        return cls(
            id=str(data.get('id', '') or uuid.uuid4()),
            command=str(data.get('command', '') or ''),
            sent_at=str(data.get('sent_at', '') or ''),
            command_start_row=int(data.get('command_start_row', -1) or -1),
        )
