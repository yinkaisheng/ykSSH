#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import stat
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Union

import asyncssh

from core.file_permissions import format_unix_mode
from log_util import logger


class TransferCancelled(Exception):
    """User cancelled a transfer conflict (distinct from asyncio task cancellation)."""


TransferDecision = Literal['overwrite', 'resume', 'cancel']
ConflictHandler = Callable[[str, str, bool], Union[TransferDecision, Awaitable[TransferDecision]]]
ProgressHandler = Callable[[str, str, int, int], None]

_COPY_CHUNK_SIZE = 256 * 1024


@dataclass(frozen=True)
class _RemoteEntry:
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


async def listdir(sftp: asyncssh.SFTPClient, path: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    try:
        names = await sftp.listdir(path)
    except asyncssh.SFTPError as exc:
        logger.warning(f'SFTP listdir failed for {path}: {exc}')
        return entries

    for name in sorted(names):
        if name in ('.', '..'):
            continue
        full = f'{path.rstrip("/")}/{name}' if path not in ('', '/') else f'/{name}'
        try:
            attrs = await sftp.lstat(full)
        except asyncssh.SFTPError:
            continue
        mode = attrs.permissions or 0
        is_dir = await _remote_entry_is_dir(sftp, full, attrs)
        entries.append({
            'name': name,
            'is_dir': is_dir,
            'size': attrs.size or 0,
            'mtime': float(attrs.mtime or 0),
            'perm': format_unix_mode(mode),
        })
    return entries


async def upload(
    sftp: asyncssh.SFTPClient,
    local_path: str,
    remote_path: str,
    progress_handler: Optional[ProgressHandler] = None,
    conflict_handler: Optional[ConflictHandler] = None,
) -> None:
    local_path = os.path.abspath(local_path)
    if os.path.isdir(local_path):
        logger.info(f'SFTP upload directory start: local={local_path}, remote={remote_path}')
        await _upload_dir(sftp, local_path, remote_path, progress_handler, conflict_handler)
        logger.info(f'SFTP upload directory done: local={local_path}, remote={remote_path}')
        return
    logger.info(f'SFTP upload file start: local={local_path}, remote={remote_path}')
    await _upload_file(sftp, local_path, remote_path, progress_handler, conflict_handler)
    logger.info(f'SFTP upload file done: local={local_path}, remote={remote_path}')


async def download(
    sftp: asyncssh.SFTPClient,
    remote_path: str,
    local_path: str,
    progress_handler: Optional[ProgressHandler] = None,
    conflict_handler: Optional[ConflictHandler] = None,
) -> None:
    attrs = await sftp.lstat(remote_path)
    if await _remote_entry_is_dir(sftp, remote_path, attrs):
        logger.info(f'SFTP download directory start: remote={remote_path}, local={local_path}')
        await _download_dir(sftp, remote_path, local_path, progress_handler, conflict_handler)
        logger.info(f'SFTP download directory done: remote={remote_path}, local={local_path}')
        return
    logger.info(f'SFTP download file start: remote={remote_path}, local={local_path}')
    await _download_file(sftp, remote_path, local_path, progress_handler, conflict_handler)
    logger.info(f'SFTP download file done: remote={remote_path}, local={local_path}')


async def _upload_dir(
    sftp: asyncssh.SFTPClient,
    local_dir: str,
    remote_dir: str,
    progress_handler: Optional[ProgressHandler],
    conflict_handler: Optional[ConflictHandler],
) -> None:
    if not await _ensure_remote_dir(sftp, remote_dir, local_dir, conflict_handler):
        return
    for name in os.listdir(local_dir):
        local_child = os.path.join(local_dir, name)
        remote_child = _remote_join(remote_dir, name)
        if os.path.isdir(local_child):
            await _upload_dir(sftp, local_child, remote_child, progress_handler, conflict_handler)
        else:
            await _upload_file(sftp, local_child, remote_child, progress_handler, conflict_handler)
    await _set_remote_mtime(sftp, remote_dir, os.path.getmtime(local_dir))


async def _upload_file(
    sftp: asyncssh.SFTPClient,
    local_path: str,
    remote_path: str,
    progress_handler: Optional[ProgressHandler],
    conflict_handler: Optional[ConflictHandler],
) -> None:
    src_size = os.path.getsize(local_path)
    src_mtime = os.path.getmtime(local_path)
    start = await _remote_file_start_offset(
        sftp,
        local_path,
        remote_path,
        src_size,
        conflict_handler,
    )
    if start is None:
        return
    if progress_handler is not None:
        progress_handler(local_path, remote_path, start, src_size)
    mode = 'ab' if start > 0 else 'wb'
    remote_file = await sftp.open(remote_path, mode, encoding=None)
    try:
        with open(local_path, 'rb') as local_file:
            if start > 0:
                local_file.seek(start)
            copied = start
            while True:
                chunk = local_file.read(_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                await remote_file.write(chunk)
                copied += len(chunk)
                if progress_handler is not None:
                    progress_handler(local_path, remote_path, copied, src_size)
    finally:
        await remote_file.close()
    await _set_remote_mtime(sftp, remote_path, src_mtime)


async def _download_dir(
    sftp: asyncssh.SFTPClient,
    remote_dir: str,
    local_dir: str,
    progress_handler: Optional[ProgressHandler],
    conflict_handler: Optional[ConflictHandler],
) -> None:
    if not await _ensure_local_dir(sftp, remote_dir, local_dir, conflict_handler):
        return
    for entry in await _remote_entries(sftp, remote_dir):
        name = entry.path.rsplit('/', 1)[-1]
        local_child = os.path.join(local_dir, name)
        if entry.is_dir:
            await _download_dir(sftp, entry.path, local_child, progress_handler, conflict_handler)
        else:
            await _download_file(sftp, entry.path, local_child, progress_handler, conflict_handler)
    attrs = await sftp.lstat(remote_dir)
    _set_local_mtime(local_dir, float(attrs.mtime or 0))


async def _download_file(
    sftp: asyncssh.SFTPClient,
    remote_path: str,
    local_path: str,
    progress_handler: Optional[ProgressHandler],
    conflict_handler: Optional[ConflictHandler],
) -> None:
    attrs = await _remote_target_attrs(sftp, remote_path)
    src_size = int(attrs.size or 0)
    src_mtime = float(attrs.mtime or 0)
    start = await _local_file_start_offset(
        remote_path,
        local_path,
        src_size,
        conflict_handler,
    )
    if start is None:
        return
    if progress_handler is not None:
        progress_handler(remote_path, local_path, start, src_size)
    os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
    remote_file = await sftp.open(remote_path, 'rb', encoding=None)
    try:
        mode = 'ab' if start > 0 else 'wb'
        with open(local_path, mode) as local_file:
            copied = start
            offset = start
            while True:
                chunk = await remote_file.read(_COPY_CHUNK_SIZE, offset)
                if not chunk:
                    break
                local_file.write(chunk)
                copied += len(chunk)
                offset += len(chunk)
                if progress_handler is not None:
                    progress_handler(remote_path, local_path, copied, src_size)
    finally:
        await remote_file.close()
    _set_local_mtime(local_path, src_mtime)


async def _remote_file_start_offset(
    sftp: asyncssh.SFTPClient,
    local_path: str,
    remote_path: str,
    src_size: int,
    conflict_handler: Optional[ConflictHandler],
) -> Optional[int]:
    try:
        attrs = await sftp.lstat(remote_path)
    except asyncssh.SFTPError:
        return 0
    is_dir = stat.S_ISDIR(attrs.permissions or 0)
    decision = await _resolve_conflict(local_path, remote_path, is_dir, conflict_handler)
    if decision == 'cancel':
        raise TransferCancelled()
    if decision == 'overwrite':
        if is_dir:
            await delete_path(sftp, remote_path)
        return 0
    if is_dir:
        return None
    existing_size = int(attrs.size or 0)
    return existing_size if existing_size <= src_size else 0


async def _local_file_start_offset(
    remote_path: str,
    local_path: str,
    src_size: int,
    conflict_handler: Optional[ConflictHandler],
) -> Optional[int]:
    if not os.path.exists(local_path):
        return 0
    is_dir = os.path.isdir(local_path)
    decision = await _resolve_conflict(remote_path, local_path, is_dir, conflict_handler)
    if decision == 'cancel':
        raise TransferCancelled()
    if decision == 'overwrite':
        if is_dir:
            shutil.rmtree(local_path)
        return 0
    if is_dir:
        return None
    existing_size = os.path.getsize(local_path)
    return existing_size if existing_size <= src_size else 0


async def _ensure_remote_dir(
    sftp: asyncssh.SFTPClient,
    remote_dir: str,
    local_source: str,
    conflict_handler: Optional[ConflictHandler],
) -> bool:
    source_is_dir = os.path.isdir(local_source)
    try:
        attrs = await sftp.lstat(remote_dir)
    except asyncssh.SFTPError:
        logger.info(f'SFTP create remote directory: remote={remote_dir}')
        await sftp.makedirs(remote_dir, exist_ok=True)
        return True
    target_is_dir = stat.S_ISDIR(attrs.permissions or 0)
    if target_is_dir:
        decision = await _resolve_conflict(local_source, remote_dir, True, conflict_handler)
        if decision == 'cancel':
            raise TransferCancelled()
        if decision == 'overwrite':
            logger.info(f'SFTP overwrite remote directory: remote={remote_dir}, source={local_source}')
            await delete_path(sftp, remote_dir)
            await sftp.makedirs(remote_dir, exist_ok=True)
        return True
    decision = await _resolve_conflict(local_source, remote_dir, False, conflict_handler)
    if decision == 'cancel':
        raise TransferCancelled()
    if decision == 'overwrite':
        logger.info(f'SFTP overwrite remote path with directory: remote={remote_dir}, source={local_source}')
        await delete_path(sftp, remote_dir)
        await sftp.makedirs(remote_dir, exist_ok=True)
        return True
    if source_is_dir:
        return False
    return True


async def _ensure_local_dir(
    sftp: asyncssh.SFTPClient,
    remote_source: str,
    local_dir: str,
    conflict_handler: Optional[ConflictHandler],
) -> bool:
    del sftp
    source_is_dir = True
    if not os.path.exists(local_dir):
        logger.info(f'Create local directory for download: local={local_dir}')
        os.makedirs(local_dir, exist_ok=True)
        return True
    if os.path.isdir(local_dir):
        decision = await _resolve_conflict(remote_source, local_dir, True, conflict_handler)
        if decision == 'cancel':
            raise TransferCancelled()
        if decision == 'overwrite':
            logger.info(f'Overwrite local directory for download: local={local_dir}, source={remote_source}')
            shutil.rmtree(local_dir)
            os.makedirs(local_dir, exist_ok=True)
        return True
    decision = await _resolve_conflict(remote_source, local_dir, False, conflict_handler)
    if decision == 'cancel':
        raise TransferCancelled()
    if decision == 'overwrite':
        logger.info(f'Overwrite local path with directory for download: local={local_dir}, source={remote_source}')
        os.remove(local_dir)
        os.makedirs(local_dir, exist_ok=True)
        return True
    if source_is_dir:
        return False
    return True


async def _resolve_conflict(
    source_path: str,
    target_path: str,
    target_is_dir: bool,
    conflict_handler: Optional[ConflictHandler],
) -> TransferDecision:
    if conflict_handler is None:
        return 'overwrite'
    result = conflict_handler(source_path, target_path, target_is_dir)
    if inspect.isawaitable(result):
        return await result
    return result


async def _remote_entries(
    sftp: asyncssh.SFTPClient,
    path: str,
    *,
    follow_symlink_dirs: bool = True,
) -> list[_RemoteEntry]:
    entries: list[_RemoteEntry] = []
    for name in await sftp.listdir(path):
        if name in ('.', '..'):
            continue
        full = _remote_join(path, name)
        attrs = await sftp.lstat(full)
        mode = attrs.permissions or 0
        is_dir = stat.S_ISDIR(mode)
        if follow_symlink_dirs and stat.S_ISLNK(mode):
            is_dir = await _remote_entry_is_dir(sftp, full, attrs)
        entries.append(_RemoteEntry(
            path=full,
            is_dir=is_dir,
            size=int(attrs.size or 0),
            mtime=float(attrs.mtime or 0),
        ))
    return entries


async def _remote_entry_is_dir(
    sftp: asyncssh.SFTPClient,
    path: str,
    attrs: asyncssh.SFTPAttrs,
) -> bool:
    mode = attrs.permissions or 0
    if stat.S_ISDIR(mode):
        return True
    if not stat.S_ISLNK(mode):
        return False
    try:
        target_attrs = await sftp.stat(path)
    except asyncssh.SFTPError:
        return False
    return stat.S_ISDIR(target_attrs.permissions or 0)


async def _remote_target_attrs(sftp: asyncssh.SFTPClient, path: str) -> asyncssh.SFTPAttrs:
    attrs = await sftp.lstat(path)
    if not stat.S_ISLNK(attrs.permissions or 0):
        return attrs
    try:
        return await sftp.stat(path)
    except asyncssh.SFTPError:
        return attrs


def _remote_join(base: str, name: str) -> str:
    base = base.rstrip('/')
    return f'{base}/{name}' if base else f'/{name}'


async def _set_remote_mtime(sftp: asyncssh.SFTPClient, path: str, mtime: float) -> None:
    try:
        await sftp.setstat(path, asyncssh.SFTPAttrs(mtime=int(mtime)))
    except Exception as exc:
        logger.warning(f'failed to set remote mtime for {path}: {exc}')


def _set_local_mtime(path: str, mtime: float) -> None:
    if mtime <= 0:
        return
    try:
        os.utime(path, (mtime, mtime))
    except OSError as exc:
        logger.warning(f'failed to set local mtime for {path}: {exc}')


async def delete(sftp: asyncssh.SFTPClient, remote_path: str) -> None:
    """Delete a remote path. Symlinks are removed without following the target."""
    try:
        attrs = await sftp.lstat(remote_path)
    except asyncssh.SFTPError as exc:
        raise exc
    mode = attrs.permissions or 0
    if stat.S_ISLNK(mode):
        logger.info(f'SFTP delete symlink: remote={remote_path}')
        await sftp.remove(remote_path)
        return
    if stat.S_ISDIR(mode):
        logger.info(f'SFTP delete directory: remote={remote_path}')
        for entry in await _remote_entries(sftp, remote_path, follow_symlink_dirs=False):
            await delete(sftp, entry.path)
        await sftp.rmdir(remote_path)
        return
    logger.info(f'SFTP delete file: remote={remote_path}')
    await sftp.remove(remote_path)


async def rename(sftp: asyncssh.SFTPClient, old_path: str, new_path: str) -> None:
    logger.info(f'SFTP rename: old={old_path}, new={new_path}')
    await sftp.rename(old_path, new_path)


async def mkdir(sftp: asyncssh.SFTPClient, remote_path: str) -> None:
    logger.info(f'SFTP mkdir: remote={remote_path}')
    await sftp.mkdir(remote_path)


delete_path = delete
