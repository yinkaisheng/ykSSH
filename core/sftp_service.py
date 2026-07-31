#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import stat
from typing import Any, Dict, List, Optional

import asyncssh

from log_util import logger


async def listdir(sftp: asyncssh.SFTPClient, path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    try:
        names = await sftp.listdir(path)
    except asyncssh.SFTPError as exc:
        logger.warning(f'SFTP listdir failed for {path}: {exc}')
        return entries

    for name in sorted(names):
        full = f'{path.rstrip("/")}/{name}' if path not in ('', '/') else f'/{name}'
        try:
            attrs = await sftp.stat(full)
        except asyncssh.SFTPError:
            continue
        is_dir = stat.S_ISDIR(attrs.permissions or 0)
        entries.append({
            'name': name,
            'is_dir': is_dir,
            'size': attrs.size or 0,
            'mtime': float(attrs.mtime or 0),
            'perm': oct(attrs.permissions or 0)[-3:],
        })
    return entries


async def upload(sftp: asyncssh.SFTPClient, local_path: str, remote_path: str) -> None:
    await sftp.put(local_path, remote_path)


async def download(sftp: asyncssh.SFTPClient, remote_path: str, local_path: str) -> None:
    await sftp.get(remote_path, local_path)


async def delete(sftp: asyncssh.SFTPClient, remote_path: str) -> None:
    try:
        attrs = await sftp.stat(remote_path)
    except asyncssh.SFTPError as exc:
        raise exc
    if stat.S_ISDIR(attrs.permissions or 0):
        await sftp.rmdir(remote_path)
    else:
        await sftp.remove(remote_path)


async def rename(sftp: asyncssh.SFTPClient, old_path: str, new_path: str) -> None:
    await sftp.rename(old_path, new_path)


async def mkdir(sftp: asyncssh.SFTPClient, remote_path: str) -> None:
    await sftp.mkdir(remote_path)


def run_sync(coro):
    """Run coroutine on the current asyncio event loop."""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        return asyncio.ensure_future(coro)
    return loop.run_until_complete(coro)


delete_path = delete
