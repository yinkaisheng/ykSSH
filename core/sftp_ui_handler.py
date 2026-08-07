#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import stat
import time
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QWidget

from core.sftp_service import (
    TransferCancelled,
    TransferDecision,
    LocalSymlinkUnsupported,
    delete_path,
    download,
    mkdir,
    rename,
    upload,
)
from core.connection_manager import ConnectionManager
from core.file_permissions import PermissionChange
from i18n import tr
from log_util import logger
from ui.dialog_i18n import ask_transfer_conflict_async, message_warning_async


def _remote_parent_and_name(path: str) -> tuple[str, str]:
    text = (path or '').strip()
    if not text or text == '/':
        return '/', ''
    text = text.rstrip('/')
    parent, _, name = text.rpartition('/')
    if not parent:
        parent = '/'
    return parent, name


class SftpUiHandler(QObject):
    transfer_status_changed = pyqtSignal(str, str, bool, float, int, int)
    property_status_changed = pyqtSignal(bool, int, int, int)

    def __init__(
        self,
        tab_id: str,
        connection_manager: ConnectionManager,
        on_refresh_ui: Callable[[], None],
        parent: QObject = None,
    ) -> None:
        super().__init__(parent)
        self.tab_id = tab_id
        self._cm = connection_manager
        self._on_refresh_ui = on_refresh_ui
        self._local_dir = os.path.expanduser('~')
        self._remote_dir = '/'
        self._remote_home = '/'
        self._paths_initialized = False
        self._transfer_stats: Dict[str, dict] = {}
        self._transfer_conflict_policy: Dict[str, Optional[TransferDecision]] = {
            'upload': None,
            'download': None,
        }
        self._transfer_tasks: set[asyncio.Task] = set()
        self._background_tasks: set[asyncio.Task] = set()

    @property
    def local_dir(self) -> str:
        return self._local_dir

    @property
    def remote_dir(self) -> str:
        return self._remote_dir

    @property
    def remote_home(self) -> str:
        return self._remote_home

    async def resolve_remote_navigation_target(self, path: str) -> tuple[str, str, Optional[bool]]:
        """Return (directory, filename_to_select, is_file) for a remote favorite/path."""
        text = (path or '').strip() or '/'
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return text, '', None
        sftp = ssh.get_sftp()
        if sftp is None:
            return text, '', None

        probe = text
        while True:
            try:
                attrs = await sftp.lstat(probe)
            except Exception:
                if probe == '/':
                    return '/', '', None
                probe, _name = _remote_parent_and_name(probe)
                continue
            mode = attrs.permissions or 0
            if stat.S_ISLNK(mode):
                try:
                    attrs = await sftp.stat(probe)
                    mode = attrs.permissions or 0
                except Exception:
                    pass
            if stat.S_ISDIR(mode):
                return probe, '', False if probe == text else None
            parent, name = _remote_parent_and_name(probe)
            return parent, name, True if probe == text else None

    def try_init_session_paths(self, local_path: str, remote_path: str) -> bool:
        if self._paths_initialized:
            return False
        self._local_dir = local_path
        self._remote_dir = remote_path or '/'
        self._remote_home = self._remote_dir
        self._paths_initialized = True
        return True

    def set_local_dir(self, path: str) -> None:
        self._local_dir = path
        self._paths_initialized = True

    def set_remote_dir(self, path: str) -> None:
        self._remote_dir = path or '/'
        self._paths_initialized = True

    def refresh_remote(self, path: str) -> None:
        self.set_remote_dir(path)
        self._track_background_task(
            asyncio.create_task(self._cm.refresh_remote_list(self.tab_id, self._remote_dir))
        )

    def upload_local_paths(self, local_paths: List[str]) -> None:
        logger.info(
            'Upload requested: '
            f'tab_id={self.tab_id}, count={len(local_paths)}, remote_dir={self._remote_dir}, '
            f'paths={local_paths}'
        )
        self._track_transfer_task(asyncio.create_task(self._upload_async(local_paths)))

    def _track_transfer_task(self, task: asyncio.Task) -> None:
        self._transfer_tasks.add(task)
        task.add_done_callback(lambda done: self._task_done(done, self._transfer_tasks))

    def _track_background_task(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(lambda done: self._task_done(done, self._background_tasks))

    def _task_done(self, task: asyncio.Task, tasks: set[asyncio.Task]) -> None:
        tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error(f'SFTP UI task failed: tab_id={self.tab_id}, error={error}')

    def has_running_transfers(self) -> bool:
        return any(not task.done() for task in self._transfer_tasks)

    def cancel_transfers(self) -> None:
        logger.info(f'Cancel transfer tasks requested: tab_id={self.tab_id}, count={len(self._transfer_tasks)}')
        for task in list(self._transfer_tasks):
            if not task.done():
                task.cancel()
        for task in list(self._background_tasks):
            if not task.done():
                task.cancel()

    async def wait_transfers_closed(self) -> None:
        tasks = [
            task for task in (*self._transfer_tasks, *self._background_tasks)
            if not task.done()
        ]
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _format_speed(bytes_per_second: float) -> str:
        if bytes_per_second < 1024:
            return f'{bytes_per_second:.0f} B/s'
        if bytes_per_second < 1024 * 1024:
            return f'{bytes_per_second / 1024:.1f} KB/s'
        return f'{bytes_per_second / (1024 * 1024):.1f} MB/s'

    @staticmethod
    def _local_path_size(path: str) -> int:
        try:
            if SftpUiHandler._is_local_link(path):
                return 0
            if os.path.isdir(path):
                total = 0
                for root, dirs, files in os.walk(path):
                    dirs[:] = [
                        name for name in dirs
                        if not SftpUiHandler._is_local_link(os.path.join(root, name))
                    ]
                    for name in files:
                        try:
                            total += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            continue
                return total
            return os.path.getsize(path)
        except OSError:
            return 0

    @staticmethod
    def _is_local_link(path: str) -> bool:
        if os.path.islink(path):
            return True
        isjunction = getattr(os.path, 'isjunction', None)
        return bool(isjunction is not None and isjunction(path))

    async def _remote_path_size(
        self,
        sftp,
        path: str,
        visited_dirs: Optional[set[str]] = None,
    ) -> int:
        if visited_dirs is None:
            visited_dirs = set()
        try:
            attrs = await sftp.lstat(path)
        except Exception:
            return 0
        mode = attrs.permissions or 0
        if stat.S_ISLNK(mode):
            try:
                target_attrs = await sftp.stat(path)
            except Exception:
                return int(attrs.size or 0)
            mode = target_attrs.permissions or 0
            if not stat.S_ISDIR(mode):
                return int(target_attrs.size or 0)
        if not stat.S_ISDIR(mode):
            return int(attrs.size or 0)
        try:
            canonical = await sftp.realpath(path)
        except Exception:
            canonical = path
        if canonical in visited_dirs:
            logger.warning(f'Remote size skipped recursive symlink directory: tab_id={self.tab_id}, path={path}')
            return 0
        visited_dirs.add(canonical)
        total = 0
        try:
            names = await sftp.listdir(path)
        except Exception:
            return total
        for name in names:
            if name in ('.', '..'):
                continue
            child = f'{path.rstrip("/")}/{name}' if path not in ('', '/') else f'/{name}'
            total += await self._remote_path_size(sftp, child, visited_dirs)
        return total

    def _begin_transfer_status(self, kind: str, total_bytes: int) -> None:
        self._transfer_stats[kind] = {
            'last_time': time.monotonic(),
            'last_bytes': 0,
            'total_bytes': max(0, int(total_bytes)),
            'paths': {},
        }
        self.transfer_status_changed.emit(kind, '', True, 0.0, 0, max(0, int(total_bytes)))

    def _make_progress_handler(self, kind: str):
        def _on_progress(src, _dst, bytes_so_far: int, _total: int) -> None:
            stats = self._transfer_stats.get(kind)
            if stats is None:
                return
            path_key = os.fsdecode(src)
            paths = stats['paths']
            is_new_path = path_key not in paths
            paths[path_key] = max(int(bytes_so_far), int(paths.get(path_key, 0)))
            current_total = sum(int(value) for value in paths.values())
            now = time.monotonic()
            if is_new_path and int(bytes_so_far) > 0:
                stats['last_time'] = now
                stats['last_bytes'] = current_total
                total_bytes = int(stats['total_bytes'])
                progress = min(1.0, current_total / total_bytes) if total_bytes > 0 else 0.0
                self.transfer_status_changed.emit(kind, '', True, progress, current_total, total_bytes)
                return
            elapsed = max(0.001, now - float(stats['last_time']))
            delta = max(0, current_total - int(stats['last_bytes']))
            if elapsed < 0.2 and delta > 0:
                return
            stats['last_time'] = now
            stats['last_bytes'] = current_total
            total_bytes = int(stats['total_bytes'])
            progress = min(1.0, current_total / total_bytes) if total_bytes > 0 else 0.0
            self.transfer_status_changed.emit(
                kind,
                self._format_speed(delta / elapsed),
                True,
                progress,
                current_total,
                total_bytes,
            )

        return _on_progress

    def _end_transfer_status(self, kind: str) -> None:
        self._transfer_stats.pop(kind, None)
        self.transfer_status_changed.emit(kind, '', False, 0.0, 0, 0)

    def _dialog_parent(self) -> Optional[QWidget]:
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    def _make_conflict_handler(self, kind: str):
        async def _on_conflict(
            source_path: str,
            target_path: str,
            target_is_dir: bool,
        ) -> TransferDecision:
            policy = self._transfer_conflict_policy.get(kind)
            if policy is not None:
                return policy
            choice = await ask_transfer_conflict_async(
                self._dialog_parent(),
                tr('file.conflict_title'),
                tr(
                    'file.conflict_body',
                    target=target_path,
                    kind=tr('file.folder') if target_is_dir else tr('file.file'),
                ),
            )
            logger.info(
                'Transfer conflict resolved: '
                f'tab_id={self.tab_id}, kind={kind}, source={source_path}, '
                f'target={target_path}, target_is_dir={target_is_dir}, choice={choice}'
            )
            if choice == 'overwrite_all':
                self._transfer_conflict_policy[kind] = 'overwrite'
                return 'overwrite'
            if choice == 'resume_all':
                self._transfer_conflict_policy[kind] = 'resume'
                return 'resume'
            if choice in ('overwrite', 'resume'):
                return choice
            return 'cancel'

        return _on_conflict

    async def _warn(self, message: str) -> None:
        await message_warning_async(self._dialog_parent(), tr('file.operation_failed'), message)

    async def _upload_async(self, local_paths: List[str]) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        remote_dir = self._remote_dir.rstrip('/') or '/'
        total_bytes = sum(self._local_path_size(path) for path in local_paths)
        self._transfer_conflict_policy['upload'] = None
        self._begin_transfer_status('upload', total_bytes)
        logger.info(
            'Upload batch start: '
            f'tab_id={self.tab_id}, count={len(local_paths)}, total_bytes={total_bytes}, '
            f'remote_dir={remote_dir}'
        )
        user_cancelled = False
        try:
            for local in local_paths:
                name = os.path.basename(local.rstrip(os.sep))
                remote = f'{remote_dir}/{name}' if remote_dir != '/' else f'/{name}'
                try:
                    await upload(
                        sftp,
                        local,
                        remote,
                        self._make_progress_handler('upload'),
                        self._make_conflict_handler('upload'),
                    )
                    logger.info(f'Upload item done: tab_id={self.tab_id}, local={local}, remote={remote}')
                except TransferCancelled:
                    logger.info(f'Upload batch user-cancelled: tab_id={self.tab_id}, remote_dir={remote_dir}')
                    user_cancelled = True
                    break
                except LocalSymlinkUnsupported:
                    logger.warning(f'Upload skipped local symlink or junction: tab_id={self.tab_id}, local={local}')
                    await self._warn(tr('file.upload_symlink_unsupported', path=local))
                except asyncio.CancelledError:
                    logger.info(f'Upload batch cancelled: tab_id={self.tab_id}, remote_dir={remote_dir}')
                    raise
                except Exception as exc:
                    logger.warning(f'upload failed: {exc}')
                    await self._warn(str(exc))
            self._cm.invalidate_remote_cache(self.tab_id)
            await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
            self._on_refresh_ui()
            if user_cancelled:
                logger.info(
                    'Upload batch stopped by user: '
                    f'tab_id={self.tab_id}, count={len(local_paths)}, total_bytes={total_bytes}, '
                    f'remote_dir={remote_dir}'
                )
            else:
                logger.info(
                    'Upload batch done: '
                    f'tab_id={self.tab_id}, count={len(local_paths)}, total_bytes={total_bytes}, '
                    f'remote_dir={remote_dir}'
                )
        finally:
            self._end_transfer_status('upload')

    def download_remote_paths(self, remote_paths: List[str]) -> None:
        self._download_remote_paths(remote_paths, self._local_dir)

    def download_remote_paths_to(self, remote_paths: List[str], local_dir: str) -> None:
        self._download_remote_paths(remote_paths, local_dir)

    def _download_remote_paths(self, remote_paths: List[str], local_dir: str) -> None:
        logger.info(
            'Download requested: '
            f'tab_id={self.tab_id}, count={len(remote_paths)}, local_dir={local_dir}, '
            f'paths={remote_paths}'
        )
        self._track_transfer_task(asyncio.create_task(self._download_async(remote_paths, local_dir)))

    async def _download_async(self, remote_paths: List[str], local_dir: str) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        total_bytes = 0
        visited_dirs: set[str] = set()
        for remote in remote_paths:
            total_bytes += await self._remote_path_size(sftp, remote, visited_dirs)
        self._transfer_conflict_policy['download'] = None
        self._begin_transfer_status('download', total_bytes)
        logger.info(
            'Download batch start: '
            f'tab_id={self.tab_id}, count={len(remote_paths)}, total_bytes={total_bytes}, '
            f'local_dir={local_dir}'
        )
        user_cancelled = False
        try:
            for remote in remote_paths:
                name = os.path.basename(remote.rstrip('/'))
                local = os.path.join(local_dir, name)
                try:
                    await download(
                        sftp,
                        remote,
                        local,
                        self._make_progress_handler('download'),
                        self._make_conflict_handler('download'),
                    )
                    logger.info(f'Download item done: tab_id={self.tab_id}, remote={remote}, local={local}')
                except TransferCancelled:
                    logger.info(f'Download batch user-cancelled: tab_id={self.tab_id}, local_dir={local_dir}')
                    user_cancelled = True
                    break
                except asyncio.CancelledError:
                    logger.info(f'Download batch cancelled: tab_id={self.tab_id}, local_dir={local_dir}')
                    raise
                except Exception as exc:
                    logger.warning(f'download failed: {exc}')
                    await self._warn(str(exc))
            self._on_refresh_ui()
            if user_cancelled:
                logger.info(
                    'Download batch stopped by user: '
                    f'tab_id={self.tab_id}, count={len(remote_paths)}, total_bytes={total_bytes}, '
                    f'local_dir={local_dir}'
                )
            else:
                logger.info(
                    'Download batch done: '
                    f'tab_id={self.tab_id}, count={len(remote_paths)}, total_bytes={total_bytes}, '
                    f'local_dir={local_dir}'
                )
        finally:
            self._end_transfer_status('download')

    def delete_remote_paths(self, remote_paths: List[str]) -> None:
        logger.info(f'Remote delete requested: tab_id={self.tab_id}, count={len(remote_paths)}, paths={remote_paths}')
        self._track_transfer_task(asyncio.create_task(self._delete_async(remote_paths)))

    async def _delete_async(self, remote_paths: List[str]) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        logger.info(f'Remote delete batch start: tab_id={self.tab_id}, count={len(remote_paths)}')
        try:
            for remote in remote_paths:
                try:
                    await delete_path(sftp, remote)
                    logger.info(f'Remote delete item done: tab_id={self.tab_id}, remote={remote}')
                except asyncio.CancelledError:
                    logger.info(f'Remote delete batch cancelled: tab_id={self.tab_id}')
                    raise
                except Exception as exc:
                    logger.warning(f'delete failed: {exc}')
                    await self._warn(str(exc))
            self._cm.invalidate_remote_cache(self.tab_id)
            await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
            self._on_refresh_ui()
            logger.info(f'Remote delete batch done: tab_id={self.tab_id}, count={len(remote_paths)}')
        except asyncio.CancelledError:
            self._cm.invalidate_remote_cache(self.tab_id)
            raise

    def rename_remote(self, old_name: str, new_name: str) -> None:
        logger.info(
            'Remote rename requested: '
            f'tab_id={self.tab_id}, remote_dir={self._remote_dir}, old_name={old_name}, new_name={new_name}'
        )
        self._track_transfer_task(asyncio.create_task(self._rename_async(old_name, new_name)))

    def apply_remote_properties(self, remote_paths: List[str], change: PermissionChange) -> None:
        logger.info(
            'Remote properties requested: '
            f'tab_id={self.tab_id}, count={len(remote_paths)}, recursive={change.recursive}'
        )
        self._track_transfer_task(
            asyncio.create_task(self._apply_remote_properties_async(remote_paths, change))
        )

    async def _apply_remote_properties_async(
        self,
        remote_paths: List[str],
        change: PermissionChange,
    ) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None or ssh.get_sftp() is None:
            return
        sftp = ssh.get_sftp()
        self.property_status_changed.emit(True, 0, 0, 0)
        failed = 0
        try:
            targets: list[tuple[str, int]] = []
            seen: set[str] = set()

            async def collect(path: str, recurse: bool) -> None:
                nonlocal failed
                if path in seen:
                    return
                seen.add(path)
                try:
                    attrs = await sftp.lstat(path)
                except Exception as exc:
                    failed += 1
                    logger.warning(f'Remote properties stat failed: path={path}, error={exc}')
                    return
                mode = int(attrs.permissions or 0)
                targets.append((path, mode))
                self.property_status_changed.emit(True, len(targets), 0, failed)
                if not recurse or not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                    return
                try:
                    entries = await sftp.readdir(path)
                except Exception as exc:
                    failed += 1
                    logger.warning(f'Remote properties list failed: path={path}, error={exc}')
                    return
                for entry in entries:
                    name = str(entry.filename)
                    if name in ('.', '..'):
                        continue
                    base = path.rstrip('/')
                    child = f'{base}/{name}' if base else f'/{name}'
                    await collect(child, True)

            for remote_path in dict.fromkeys(remote_paths):
                await collect(remote_path, change.recursive)

            total = len(targets)
            self.property_status_changed.emit(True, 0, total, failed)

            async def chmod_one(path: str, mode: int) -> bool:
                try:
                    await sftp.chmod(path, change.apply(mode), follow_symlinks=False)
                    return True
                except Exception as exc:
                    logger.warning(f'Remote chmod failed: path={path}, error={exc}')
                    return False

            done = 0
            for offset in range(0, total, 16):
                batch = targets[offset:offset + 16]
                results = await asyncio.gather(
                    *(chmod_one(path, mode) for path, mode in batch),
                )
                done += len(batch)
                failed += sum(1 for result in results if not result)
                self.property_status_changed.emit(True, done, total, failed)

            self._cm.invalidate_remote_cache(self.tab_id)
            await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
            self._on_refresh_ui()
            if failed:
                await self._warn(tr('file.properties.failed', count=failed))
        except asyncio.CancelledError:
            raise
        finally:
            self.property_status_changed.emit(False, 0, 0, 0)

    async def _rename_async(self, old_name: str, new_name: str) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        base = self._remote_dir.rstrip('/') or ''
        old_path = f'{base}/{old_name}' if base else f'/{old_name}'
        new_path = f'{base}/{new_name}' if base else f'/{new_name}'
        try:
            await rename(sftp, old_path, new_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f'rename failed: old={old_path}, new={new_path}, error={exc}')
            await self._warn(str(exc))
            return
        self._cm.invalidate_remote_cache(self.tab_id)
        await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
        self._on_refresh_ui()
        logger.info(f'Remote rename done: tab_id={self.tab_id}, old={old_path}, new={new_path}')

    def mkdir_remote(self, name: str) -> None:
        logger.info(f'Remote mkdir requested: tab_id={self.tab_id}, remote_dir={self._remote_dir}, name={name}')
        self._track_transfer_task(asyncio.create_task(self._mkdir_async(name)))

    async def _mkdir_async(self, name: str) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        base = self._remote_dir.rstrip('/') or ''
        path = f'{base}/{name}' if base else f'/{name}'
        try:
            await mkdir(sftp, path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f'mkdir failed: remote={path}, error={exc}')
            await self._warn(str(exc))
            return
        self._cm.invalidate_remote_cache(self.tab_id)
        await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
        self._on_refresh_ui()
        logger.info(f'Remote mkdir done: tab_id={self.tab_id}, remote={path}')
