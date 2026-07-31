#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import stat
from typing import Optional, Union

import asyncssh


def resolve_local_path(configured: str) -> str:
    """Return configured local directory, or user home if missing/invalid."""
    default = os.path.expanduser('~')
    text = (configured or '').strip()
    if not text:
        return default
    candidate = os.path.normpath(os.path.expanduser(text))
    if os.path.isdir(candidate):
        return candidate
    return default


def _path_text(value: Union[str, bytes, asyncssh.sftp.SFTPName, None]) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    filename = getattr(value, 'filename', None)
    if filename is not None:
        return _path_text(filename)
    return str(value)


async def resolve_remote_path(
    sftp: asyncssh.SFTPClient,
    configured: str,
    *,
    username: str = '',
) -> str:
    """Return configured remote directory, or remote home if missing/invalid."""
    default = await _remote_home(sftp, username=username)
    text = (configured or '').strip()
    if not text:
        return default
    candidate = await _expand_remote_path(sftp, text, default)
    try:
        attrs = await sftp.stat(candidate)
        if stat.S_ISDIR(attrs.permissions or 0):
            return candidate
    except asyncssh.SFTPError:
        pass
    return default


async def _expand_remote_path(
    sftp: asyncssh.SFTPClient,
    path: str,
    home: str,
) -> str:
    if not path.startswith('~'):
        return path
    if path in ('~', '~/'):
        return home
    if path.startswith('~/'):
        suffix = path[2:]
        composed = sftp.compose_path(suffix, home.encode('utf-8'))
        return _path_text(composed)
    try:
        return _path_text(await sftp.realpath(path))
    except asyncssh.SFTPError:
        return home


async def _remote_home(sftp: asyncssh.SFTPClient, *, username: str = '') -> str:
    for probe in ('.', '~'):
        try:
            resolved = _path_text(await sftp.realpath(probe))
            if resolved:
                return resolved
        except asyncssh.SFTPError:
            continue

    try:
        cwd = _path_text(await sftp.getcwd())
        if cwd:
            return cwd
    except asyncssh.SFTPError:
        pass

    user = (username or '').strip()
    if user:
        for candidate in (f'/home/{user}', f'/Users/{user}'):
            try:
                attrs = await sftp.stat(candidate)
                if stat.S_ISDIR(attrs.permissions or 0):
                    return candidate
            except asyncssh.SFTPError:
                continue

    return '/'
