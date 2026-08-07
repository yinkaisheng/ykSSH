#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


def _resolve_app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = _resolve_app_dir()
DATA_DIR = APP_DIR / 'config'
LOGS_DIR = APP_DIR / 'logs'
CONFIG_FILE = DATA_DIR / 'config.json'
SESSIONS_FILE = DATA_DIR / 'sessions.json'
COMMANDS_FILE = DATA_DIR / 'commands.json'
CREDENTIALS_FILE = DATA_DIR / 'credentials.json'
SECRET_KEY_FILE = DATA_DIR / 'secret.key'
HOST_KEYS_FILE = DATA_DIR / 'host_keys.json'
LANGUAGES_DIR = APP_DIR / 'Languages'
