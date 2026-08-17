#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narrow terminal interface consumed by the connection layer."""
from __future__ import annotations

from typing import Any, Protocol


class TerminalPort(Protocol):
    """Terminal capabilities required by ``ConnectionManager``."""

    input_received: Any

    def terminal_size(self) -> tuple[int, int]:
        """Return the current terminal size as ``(columns, rows)``."""
        ...

    def write_text(self, text: str) -> None:
        """Feed decoded SSH output into the terminal."""
        ...

    def shell_prompt_ready(self) -> bool:
        """Return whether the current terminal line looks like an idle shell prompt."""
        ...
