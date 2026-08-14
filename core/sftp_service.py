#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator, Literal

import asyncssh

from core.file_permissions import format_unix_mode
from log_util import logger


class TransferCancelled(Exception):
    """User cancelled a transfer conflict (distinct from asyncio task cancellation)."""


class LocalSymlinkUnsupported(Exception):
    """A local symlink/junction was selected for upload."""


TransferDecision = Literal['overwrite', 'resume', 'cancel']
ConflictHandler = Callable[[str, str, bool], TransferDecision | Awaitable[TransferDecision]]
ProgressHandler = Callable[[str, str, int, int], None]

_COPY_CHUNK_SIZE = 256 * 1024
_SFTP_TAB_ID: ContextVar[str] = ContextVar('sftp_tab_id', default='unbound')


@contextmanager
def _sftp_log_context(tab_id: str) -> Iterator[None]:
    """Bind the owning terminal tab to logs emitted by one SFTP operation."""
    token = _SFTP_TAB_ID.set(tab_id)
    try:
        yield
    finally:
        _SFTP_TAB_ID.reset(token)


def _log_id() -> str:
    return f'tab_id={_SFTP_TAB_ID.get()}'


@dataclass(frozen=True)
class _RemoteEntry:
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


async def listdir(
    sftp: asyncssh.SFTPClient,
    path: str,
    *,
    tab_id: str,
) -> list[dict[str, Any]]:
    with _sftp_log_context(tab_id):
        return await _listdir(sftp, path)


async def _listdir(sftp: asyncssh.SFTPClient, path: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        names = await sftp.listdir(path)
    except asyncssh.SFTPError as exc:
        logger.warning(f'SFTP listdir failed: {_log_id()}, path={path}, error={exc}')
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
            'mode': mode,
        })
    return entries


async def upload(
    sftp: asyncssh.SFTPClient,
    local_path: str,
    remote_path: str,
    progress_handler: ProgressHandler | None = None,
    conflict_handler: ConflictHandler | None = None,
    *,
    tab_id: str,
) -> None:
    with _sftp_log_context(tab_id):
        await _upload(sftp, local_path, remote_path, progress_handler, conflict_handler)


async def _upload(
    sftp: asyncssh.SFTPClient,
    local_path: str,
    remote_path: str,
    progress_handler: ProgressHandler | None,
    conflict_handler: ConflictHandler | None,
) -> None:
    local_path = os.path.abspath(local_path)
    if _is_local_link(local_path):
        raise LocalSymlinkUnsupported(local_path)
    if os.path.isdir(local_path):
        logger.info(f'SFTP upload directory start: {_log_id()}, local={local_path}, remote={remote_path}')
        await _upload_dir(
            sftp,
            local_path,
            remote_path,
            progress_handler,
            conflict_handler,
            visited_dirs=set(),
        )
        logger.info(f'SFTP upload directory done: {_log_id()}, local={local_path}, remote={remote_path}')
        return
    logger.info(f'SFTP upload file start: {_log_id()}, local={local_path}, remote={remote_path}')
    await _upload_file(sftp, local_path, remote_path, progress_handler, conflict_handler)
    logger.info(f'SFTP upload file done: {_log_id()}, local={local_path}, remote={remote_path}')


async def download(
    sftp: asyncssh.SFTPClient,
    remote_path: str,
    local_path: str,
    progress_handler: ProgressHandler | None = None,
    conflict_handler: ConflictHandler | None = None,
    *,
    tab_id: str,
) -> None:
    with _sftp_log_context(tab_id):
        await _download(sftp, remote_path, local_path, progress_handler, conflict_handler)


async def _download(
    sftp: asyncssh.SFTPClient,
    remote_path: str,
    local_path: str,
    progress_handler: ProgressHandler | None,
    conflict_handler: ConflictHandler | None,
) -> None:
    attrs = await sftp.lstat(remote_path)
    if await _remote_entry_is_dir(sftp, remote_path, attrs):
        logger.info(f'SFTP download directory start: {_log_id()}, remote={remote_path}, local={local_path}')
        await _download_dir(
            sftp,
            remote_path,
            local_path,
            progress_handler,
            conflict_handler,
            visited_dirs=set(),
        )
        logger.info(f'SFTP download directory done: {_log_id()}, remote={remote_path}, local={local_path}')
        return
    logger.info(f'SFTP download file start: {_log_id()}, remote={remote_path}, local={local_path}')
    await _download_file(sftp, remote_path, local_path, progress_handler, conflict_handler)
    logger.info(f'SFTP download file done: {_log_id()}, remote={remote_path}, local={local_path}')


async def _upload_dir(
    sftp: asyncssh.SFTPClient,
    local_dir: str,
    remote_dir: str,
    progress_handler: ProgressHandler | None,
    conflict_handler: ConflictHandler | None,
    visited_dirs: set[str],
) -> None:
    canonical_dir = os.path.realpath(local_dir)
    if canonical_dir in visited_dirs:
        logger.warning(f'SFTP upload skipped recursive local directory: {_log_id()}, local={local_dir}')
        return
    visited_dirs.add(canonical_dir)
    if not await _ensure_remote_dir(sftp, remote_dir, local_dir, conflict_handler):
        return
    for name in os.listdir(local_dir):
        local_child = os.path.join(local_dir, name)
        remote_child = _remote_join(remote_dir, name)
        if _is_local_link(local_child):
            logger.warning(f'SFTP upload skipped local symlink or junction: {_log_id()}, local={local_child}')
            continue
        if os.path.isdir(local_child):
            await _upload_dir(
                sftp,
                local_child,
                remote_child,
                progress_handler,
                conflict_handler,
                visited_dirs,
            )
        else:
            await _upload_file(sftp, local_child, remote_child, progress_handler, conflict_handler)
    src_stat = os.stat(local_dir)
    await _set_remote_mtime(sftp, remote_dir, src_stat.st_mtime, src_stat.st_atime)


async def _upload_file(
    sftp: asyncssh.SFTPClient,
    local_path: str,
    remote_path: str,
    progress_handler: ProgressHandler | None,
    conflict_handler: ConflictHandler | None,
) -> None:
    src_stat = os.stat(local_path)
    src_size = src_stat.st_size
    src_mtime = src_stat.st_mtime
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
    await _set_remote_mtime(sftp, remote_path, src_mtime, src_stat.st_atime)


async def _download_dir(
    sftp: asyncssh.SFTPClient,
    remote_dir: str,
    local_dir: str,
    progress_handler: ProgressHandler | None,
    conflict_handler: ConflictHandler | None,
    visited_dirs: set[str],
) -> None:
    canonical_dir = await _remote_realpath(sftp, remote_dir)
    if canonical_dir in visited_dirs:
        logger.warning(f'SFTP download skipped recursive symlink directory: {_log_id()}, remote={remote_dir}')
        return
    visited_dirs.add(canonical_dir)
    if not await _ensure_local_dir(sftp, remote_dir, local_dir, conflict_handler):
        return
    for entry in await _remote_entries(sftp, remote_dir):
        name = entry.path.rsplit('/', 1)[-1]
        local_child = os.path.join(local_dir, name)
        if entry.is_dir:
            await _download_dir(
                sftp,
                entry.path,
                local_child,
                progress_handler,
                conflict_handler,
                visited_dirs,
            )
        else:
            await _download_file(sftp, entry.path, local_child, progress_handler, conflict_handler)
    attrs = await sftp.lstat(remote_dir)
    _set_local_mtime(local_dir, float(attrs.mtime or 0))


async def _download_file(
    sftp: asyncssh.SFTPClient,
    remote_path: str,
    local_path: str,
    progress_handler: ProgressHandler | None,
    conflict_handler: ConflictHandler | None,
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
    conflict_handler: ConflictHandler | None,
) -> int | None:
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
            await _delete(sftp, remote_path)
        return 0
    if is_dir:
        return None
    existing_size = int(attrs.size or 0)
    return existing_size if existing_size <= src_size else 0


async def _local_file_start_offset(
    remote_path: str,
    local_path: str,
    src_size: int,
    conflict_handler: ConflictHandler | None,
) -> int | None:
    if _is_local_link(local_path):
        decision = await _resolve_conflict(remote_path, local_path, os.path.isdir(local_path), conflict_handler)
        if decision == 'cancel':
            raise TransferCancelled()
        if decision == 'overwrite':
            _remove_local_link(local_path)
            return 0
        return None
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
    conflict_handler: ConflictHandler | None,
) -> bool:
    try:
        attrs = await sftp.lstat(remote_dir)
    except asyncssh.SFTPError:
        logger.info(f'SFTP create remote directory: {_log_id()}, remote={remote_dir}')
        await sftp.makedirs(remote_dir, exist_ok=True)
        return True
    target_is_dir = stat.S_ISDIR(attrs.permissions or 0)
    if target_is_dir:
        logger.info(f'SFTP merge into remote directory: {_log_id()}, remote={remote_dir}, source={local_source}')
        return True
    decision = await _resolve_conflict(local_source, remote_dir, False, conflict_handler)
    if decision == 'cancel':
        raise TransferCancelled()
    if decision == 'overwrite':
        logger.info(f'SFTP overwrite remote path with directory: {_log_id()}, remote={remote_dir}, source={local_source}')
        await _delete(sftp, remote_dir)
        await sftp.makedirs(remote_dir, exist_ok=True)
        return True
    return False


async def _ensure_local_dir(
    sftp: asyncssh.SFTPClient,
    remote_source: str,
    local_dir: str,
    conflict_handler: ConflictHandler | None,
) -> bool:
    del sftp
    if _is_local_link(local_dir):
        decision = await _resolve_conflict(remote_source, local_dir, os.path.isdir(local_dir), conflict_handler)
        if decision == 'cancel':
            raise TransferCancelled()
        if decision == 'overwrite':
            _remove_local_link(local_dir)
            os.makedirs(local_dir, exist_ok=True)
            return True
        return False
    if not os.path.exists(local_dir):
        logger.info(f'Create local directory for download: {_log_id()}, local={local_dir}')
        os.makedirs(local_dir, exist_ok=True)
        return True
    if os.path.isdir(local_dir):
        logger.info(f'Merge download into local directory: {_log_id()}, local={local_dir}, source={remote_source}')
        return True
    decision = await _resolve_conflict(remote_source, local_dir, False, conflict_handler)
    if decision == 'cancel':
        raise TransferCancelled()
    if decision == 'overwrite':
        logger.info(f'Overwrite local path with directory for download: {_log_id()}, local={local_dir}, source={remote_source}')
        os.remove(local_dir)
        os.makedirs(local_dir, exist_ok=True)
        return True
    return False


def _is_local_link(path: str) -> bool:
    """Return whether a path is a symlink or Windows directory junction."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, 'isjunction', None)
    return bool(isjunction is not None and isjunction(path))


def _remove_local_link(path: str) -> None:
    """Remove a link itself without traversing its target."""
    isjunction = getattr(os.path, 'isjunction', None)
    if isjunction is not None and isjunction(path):
        os.rmdir(path)
    else:
        os.unlink(path)


async def _resolve_conflict(
    source_path: str,
    target_path: str,
    target_is_dir: bool,
    conflict_handler: ConflictHandler | None,
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


async def _remote_realpath(sftp: asyncssh.SFTPClient, path: str) -> str:
    try:
        return await sftp.realpath(path)
    except asyncssh.SFTPError:
        return path


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


async def _set_remote_mtime(
    sftp: asyncssh.SFTPClient,
    path: str,
    mtime: float,
    atime: float | None = None,
) -> None:
    if mtime <= 0:
        return
    try:
        await sftp.utime(path, (atime if atime is not None else mtime, mtime))
    except Exception as exc:
        logger.warning(f'Failed to set remote mtime: {_log_id()}, path={path}, error={exc}')


def _set_local_mtime(path: str, mtime: float) -> None:
    if mtime <= 0:
        return
    try:
        os.utime(path, (mtime, mtime))
    except OSError as exc:
        logger.warning(f'Failed to set local mtime: {_log_id()}, path={path}, error={exc}')


async def delete(
    sftp: asyncssh.SFTPClient,
    remote_path: str,
    *,
    tab_id: str,
) -> None:
    with _sftp_log_context(tab_id):
        await _delete(sftp, remote_path)


async def _delete(sftp: asyncssh.SFTPClient, remote_path: str) -> None:
    """Delete a remote path. Symlinks are removed without following the target."""
    try:
        attrs = await sftp.lstat(remote_path)
    except asyncssh.SFTPError as exc:
        raise exc
    mode = attrs.permissions or 0
    if stat.S_ISLNK(mode):
        logger.info(f'SFTP delete symlink: {_log_id()}, remote={remote_path}')
        await sftp.remove(remote_path)
        return
    if stat.S_ISDIR(mode):
        logger.info(f'SFTP delete directory: {_log_id()}, remote={remote_path}')
        for entry in await _remote_entries(sftp, remote_path, follow_symlink_dirs=False):
            await _delete(sftp, entry.path)
        await sftp.rmdir(remote_path)
        return
    logger.info(f'SFTP delete file: {_log_id()}, remote={remote_path}')
    await sftp.remove(remote_path)


async def rename(
    sftp: asyncssh.SFTPClient,
    old_path: str,
    new_path: str,
    *,
    tab_id: str,
) -> None:
    with _sftp_log_context(tab_id):
        logger.info(f'SFTP rename: {_log_id()}, old={old_path}, new={new_path}')
        await sftp.rename(old_path, new_path)


async def mkdir(
    sftp: asyncssh.SFTPClient,
    remote_path: str,
    *,
    tab_id: str,
) -> None:
    with _sftp_log_context(tab_id):
        logger.info(f'SFTP mkdir: {_log_id()}, remote={remote_path}')
        await sftp.mkdir(remote_path)


delete_path = delete
