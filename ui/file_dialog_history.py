#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件选择入口各自的目录记忆。"""
from __future__ import annotations

import os

from log_util import logger
from storage.app_config import get_app_config, save_file_dialog_directory


def initial_directory(purpose: str, fallback: str = '') -> str:
    """优先使用仍存在的历史目录，否则保留入口原有默认位置。"""
    directory = get_app_config().file_dialog_directories.get(purpose, '')
    return directory if directory and os.path.isdir(directory) else fallback


def remember_selection(purpose: str, selection: str, *, is_file: bool = False) -> None:
    """仅保存确认选择的目录；保存失败不阻断后续操作。"""
    if not selection:
        return
    path = os.path.abspath(selection)
    directory = os.path.dirname(path) if is_file else path
    if get_app_config().file_dialog_directories.get(purpose) == directory:
        return
    try:
        save_file_dialog_directory(purpose, directory)
    except OSError as exc:
        logger.warning(f'Failed to save file dialog directory: purpose={purpose}, error={exc}')
