#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Open local/remote files in external editors and sync remote edits."""
from __future__ import annotations

import asyncio
import hashlib
import os
import posixpath
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QFileSystemWatcher, QObject, QProcess, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QMessageBox, QWidget

from core.connection_manager import ConnectionManager
from core.sftp_service import download, upload
from i18n import tr
from log_util import logger
from storage.app_config import get_app_config
from ui.dialog_i18n import (
    ask_yes_no,
    ask_yes_no_async,
    ask_yes_no_cancel_async,
    message_warning,
    message_warning_async,
)

FileSignature = tuple[int, int]

_WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{index}' for index in range(1, 10)),
    *(f'LPT{index}' for index in range(1, 10)),
}


@dataclass
class _RemoteEditSession:
    tab_id: str
    remote_path: str
    local_path: str
    remote_signature: FileSignature
    observed_local_signature: FileSignature
    prompt_task: asyncio.Task | None = None
    pending_change: bool = False


class FileEditManager(QObject):
    """Coordinate editor launches and all remote temporary-file sessions."""

    _DEBOUNCE_MS = 800
    _STALE_TEMP_SECONDS = 7 * 24 * 60 * 60

    def __init__(
        self,
        connection_manager: ConnectionManager,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._cm = connection_manager
        self._dialog_parent = parent
        self._sessions: dict[tuple[str, str], _RemoteEditSession] = {}
        self._sessions_by_local: dict[str, _RemoteEditSession] = {}
        self._tasks: set[asyncio.Task] = set()
        self._sync_tasks: set[asyncio.Task] = set()
        self._task_tabs: dict[asyncio.Task, str | None] = {}
        self._sync_task_tabs: dict[asyncio.Task, str] = {}
        self._pending_local_paths: set[str] = set()

        self._temp_root = Path(tempfile.gettempdir()) / 'ykssh' / 'remote-edit'
        self._cleanup_stale_temp_dirs()
        self._runtime_dir = self._temp_root / uuid.uuid4().hex

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._process_pending_changes)

    def open_local_files(self, paths: list[str], use_configured_editor: bool) -> None:
        files = [os.path.abspath(path) for path in paths if os.path.isfile(path)]
        if not self._confirm_file_count(files):
            return
        self._launch_files(files, use_configured_editor)

    def open_remote_files(
        self,
        tab_id: str,
        remote_paths: list[str],
        use_configured_editor: bool,
    ) -> None:
        paths = list(dict.fromkeys(path for path in remote_paths if path))
        if not self._confirm_file_count(paths):
            return
        self._track_task(
            asyncio.create_task(self._open_remote_files_async(tab_id, paths, use_configured_editor)),
            tab_id=tab_id,
        )

    def has_running_syncs(self, tab_id: str | None = None) -> bool:
        tasks = [task for task in self._sync_tasks if not task.done()]
        if tab_id is None:
            return bool(tasks)
        return any(self._sync_task_tabs.get(task) == tab_id for task in tasks)

    def cancel_syncs(self, tab_id: str | None = None) -> None:
        for task in list(self._sync_tasks):
            if task.done():
                continue
            if tab_id is None or self._sync_task_tabs.get(task) == tab_id:
                task.cancel()

    async def close_tab(self, tab_id: str) -> None:
        self.cancel_syncs(tab_id)
        tasks = [
            task for task in self._tasks
            if not task.done() and self._task_tabs.get(task) == tab_id
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._remove_sessions(lambda session: session.tab_id == tab_id)

    async def close(self) -> None:
        self._debounce_timer.stop()
        self._watcher.blockSignals(True)
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self._remove_sessions(lambda _session: True)
        await asyncio.to_thread(self._remove_runtime_dir)

    async def _open_remote_files_async(
        self,
        tab_id: str,
        remote_paths: list[str],
        use_configured_editor: bool,
    ) -> None:
        ssh = self._cm.get_session(tab_id)
        sftp = ssh.get_sftp() if ssh is not None else None
        if sftp is None:
            await message_warning_async(
                self._dialog_parent,
                tr('file.edit_remote_title'),
                tr('file.edit_remote_not_connected'),
            )
            return

        metadata: list[tuple[str, FileSignature]] = []
        for remote_path in remote_paths:
            try:
                attrs = await sftp.stat(remote_path)
            except Exception as exc:
                logger.warning(
                    f'Remote edit stat failed: tab_id={tab_id}, path={remote_path}, error={exc}'
                )
                continue
            metadata.append((remote_path, self._remote_signature(attrs)))

        threshold_mb = get_app_config().editor.remote_large_file_mb
        threshold_bytes = threshold_mb * 1024 * 1024
        large = [
            (path, signature[0])
            for path, signature in metadata
            if signature[0] > threshold_bytes
        ]
        if large:
            names = '\n'.join(
                f'- {posixpath.basename(path)} ({self._format_size(size)})'
                for path, size in large
            )
            if not await ask_yes_no_async(
                self._dialog_parent,
                tr('file.edit_large_remote_title'),
                tr(
                    'file.edit_large_remote_confirm',
                    count=len(large),
                    limit=threshold_mb,
                    files=names,
                ),
            ):
                return

        local_paths: list[str] = []
        for remote_path, remote_signature in metadata:
            key = (tab_id, remote_path)
            existing = self._sessions.get(key)
            if existing is not None and os.path.isfile(existing.local_path):
                if existing.remote_signature != remote_signature:
                    try:
                        await download(
                            sftp, remote_path, existing.local_path, tab_id=tab_id
                        )
                        self._set_local_mtime(existing.local_path, remote_signature, tab_id)
                    except Exception as exc:
                        logger.warning(
                            f'Remote edit refresh download failed: tab_id={tab_id}, '
                            f'path={remote_path}, error={exc}'
                        )
                        await message_warning_async(
                            self._dialog_parent,
                            tr('file.edit_remote_title'),
                            tr(
                                'file.edit_remote_download_failed',
                                path=remote_path,
                                error=str(exc),
                            ),
                        )
                        continue
                    existing.remote_signature = remote_signature
                    existing.observed_local_signature = self._local_signature(
                        existing.local_path
                    )
                    self._watch_session(existing)
                local_paths.append(existing.local_path)
                continue
            local_path = self._temp_path(tab_id, remote_path)
            try:
                await download(sftp, remote_path, local_path, tab_id=tab_id)
                self._set_local_mtime(local_path, remote_signature, tab_id)
                local_signature = self._local_signature(local_path)
            except Exception as exc:
                logger.warning(
                    f'Remote edit download failed: tab_id={tab_id}, '
                    f'path={remote_path}, error={exc}'
                )
                await message_warning_async(
                    self._dialog_parent,
                    tr('file.edit_remote_title'),
                    tr('file.edit_remote_download_failed', path=remote_path, error=str(exc)),
                )
                continue
            session = _RemoteEditSession(
                tab_id=tab_id,
                remote_path=remote_path,
                local_path=local_path,
                remote_signature=remote_signature,
                observed_local_signature=local_signature,
            )
            self._sessions[key] = session
            self._sessions_by_local[os.path.normcase(local_path)] = session
            self._watch_session(session)
            local_paths.append(local_path)

        if local_paths:
            self._launch_files(local_paths, use_configured_editor)

    def _confirm_file_count(self, paths: list[str]) -> bool:
        if not paths:
            return False
        if len(paths) < 3:
            return True
        return ask_yes_no(
            self._dialog_parent,
            tr('file.edit_multiple_title'),
            tr('file.edit_multiple_confirm', count=len(paths)),
        )

    def _launch_files(self, paths: list[str], use_configured_editor: bool) -> None:
        editor_path = get_app_config().editor.executable_path.strip()
        if use_configured_editor and editor_path and os.path.isfile(editor_path):
            try:
                result = QProcess.startDetached(editor_path, paths)
                started = bool(result[0] if isinstance(result, tuple) else result)
            except Exception as exc:
                logger.warning(f'Configured editor launch failed: editor={editor_path}, error={exc}')
                started = False
            if started:
                return
        for path in paths:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                message_warning(
                    self._dialog_parent,
                    tr('file.edit_open_failed_title'),
                    tr('file.edit_open_failed', path=path),
                )

    def _watch_session(self, session: _RemoteEditSession) -> None:
        if session.local_path not in self._watcher.files():
            self._watcher.addPath(session.local_path)
        directory = os.path.dirname(session.local_path)
        if directory not in self._watcher.directories():
            self._watcher.addPath(directory)

    def _on_file_changed(self, local_path: str) -> None:
        self._queue_local_path(local_path)

    def _on_directory_changed(self, directory: str) -> None:
        directory = os.path.normcase(os.path.abspath(directory))
        for session in self._sessions.values():
            if os.path.normcase(os.path.dirname(session.local_path)) == directory:
                self._queue_local_path(session.local_path)

    def _queue_local_path(self, local_path: str) -> None:
        self._pending_local_paths.add(os.path.normcase(os.path.abspath(local_path)))
        try:
            if self._runtime_dir.exists():
                os.utime(self._runtime_dir, None)
        except OSError:
            pass
        self._debounce_timer.start(self._DEBOUNCE_MS)

    def _process_pending_changes(self) -> None:
        pending = self._pending_local_paths
        self._pending_local_paths = set()
        for normalized_path in pending:
            session = self._sessions_by_local.get(normalized_path)
            if session is None or not os.path.isfile(session.local_path):
                continue
            self._watch_session(session)
            signature = self._local_signature(session.local_path)
            if signature == session.observed_local_signature:
                continue
            if session.prompt_task is not None and not session.prompt_task.done():
                session.pending_change = True
                continue
            session.observed_local_signature = signature
            task = asyncio.create_task(self._prompt_sync_async(session, signature))
            session.prompt_task = task
            self._track_task(task, tab_id=session.tab_id)
            task.add_done_callback(lambda _done, current=session: self._prompt_done(current))

    def _prompt_done(self, session: _RemoteEditSession) -> None:
        session.prompt_task = None
        if session.pending_change:
            session.pending_change = False
            self._queue_local_path(session.local_path)

    async def _prompt_sync_async(
        self,
        session: _RemoteEditSession,
        prompted_signature: FileSignature,
    ) -> None:
        should_sync = await ask_yes_no_async(
            self._dialog_parent,
            tr('file.edit_sync_title'),
            tr('file.edit_sync_confirm', name=posixpath.basename(session.remote_path)),
            foreground=True,
        )
        if not should_sync:
            return
        sync_task = asyncio.create_task(self._sync_session_async(session, prompted_signature))
        self._sync_tasks.add(sync_task)
        self._sync_task_tabs[sync_task] = session.tab_id
        try:
            await sync_task
        finally:
            self._sync_tasks.discard(sync_task)
            self._sync_task_tabs.pop(sync_task, None)

    async def _sync_session_async(
        self,
        session: _RemoteEditSession,
        prompted_signature: FileSignature,
    ) -> None:
        ssh = self._cm.get_session(session.tab_id)
        sftp = ssh.get_sftp() if ssh is not None else None
        if sftp is None:
            await message_warning_async(
                self._dialog_parent,
                tr('file.edit_sync_title'),
                tr('file.edit_remote_not_connected'),
            )
            return
        try:
            current_attrs = await sftp.stat(session.remote_path)
            current_remote_signature = self._remote_signature(current_attrs)
            if current_remote_signature != session.remote_signature:
                decision = await ask_yes_no_cancel_async(
                    self._dialog_parent,
                    tr('file.edit_remote_changed_title'),
                    tr('file.edit_remote_changed_body'),
                )
                if decision == QMessageBox.No:
                    await download(
                        sftp,
                        session.remote_path,
                        session.local_path,
                        tab_id=session.tab_id,
                    )
                    session.remote_signature = self._remote_signature(
                        await sftp.stat(session.remote_path)
                    )
                    session.observed_local_signature = self._local_signature(session.local_path)
                    prompted_signature = session.observed_local_signature
                    self._watch_session(session)
                    await self._refresh_remote_parent(session)
                    return
                if decision != QMessageBox.Yes:
                    return
            await upload(
                sftp,
                session.local_path,
                session.remote_path,
                tab_id=session.tab_id,
            )
            session.remote_signature = self._remote_signature(await sftp.stat(session.remote_path))
            await self._refresh_remote_parent(session)
            logger.info(
                f'Remote edit sync done: tab_id={session.tab_id}, remote={session.remote_path}'
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f'Remote edit sync failed: tab_id={session.tab_id}, '
                f'path={session.remote_path}, error={exc}'
            )
            await message_warning_async(
                self._dialog_parent,
                tr('file.edit_sync_title'),
                tr('file.edit_sync_failed', path=session.remote_path, error=str(exc)),
            )
        finally:
            if os.path.isfile(session.local_path):
                current = self._local_signature(session.local_path)
                if current != prompted_signature:
                    session.pending_change = True

    async def _refresh_remote_parent(self, session: _RemoteEditSession) -> None:
        parent = posixpath.dirname(session.remote_path.rstrip('/')) or '/'
        self._cm.invalidate_remote_cache(session.tab_id, parent)
        await self._cm.refresh_remote_list(session.tab_id, parent)

    def _track_task(self, task: asyncio.Task, *, tab_id: str | None = None) -> None:
        self._tasks.add(task)
        self._task_tabs[task] = tab_id
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        tab_id = self._task_tabs.pop(task, None)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error(f'File edit task failed: tab_id={tab_id}, error={error}')

    async def _remove_sessions(self, predicate) -> None:
        sessions = [session for session in self._sessions.values() if predicate(session)]
        for session in sessions:
            if session.prompt_task is not None and not session.prompt_task.done():
                session.prompt_task.cancel()
            self._sessions.pop((session.tab_id, session.remote_path), None)
            self._sessions_by_local.pop(os.path.normcase(session.local_path), None)
            if session.local_path in self._watcher.files():
                self._watcher.removePath(session.local_path)
        if sessions:
            await asyncio.gather(
                *(session.prompt_task for session in sessions if session.prompt_task is not None),
                return_exceptions=True,
            )

    def _temp_path(self, tab_id: str, remote_path: str) -> str:
        digest = hashlib.sha256(f'{tab_id}\0{remote_path}'.encode('utf-8')).hexdigest()[:20]
        name = self._safe_temp_name(posixpath.basename(remote_path.rstrip('/')))
        directory = self._runtime_dir / digest
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory / name)

    @staticmethod
    def _safe_temp_name(remote_name: str) -> str:
        """Map a POSIX filename to a portable local filename while retaining its suffix."""
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', remote_name or '')
        name = name.rstrip(' .') or 'remote-file'
        if name.split('.', 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            name = f'_{name}'
        if len(name) > 180:
            suffix = ''.join(Path(name).suffixes[-2:])[-32:]
            stem_length = max(1, 180 - len(suffix))
            name = f'{name[:stem_length]}{suffix}'
        return name

    def _cleanup_stale_temp_dirs(self) -> None:
        try:
            entries = list(self._temp_root.iterdir()) if self._temp_root.exists() else []
        except OSError:
            return
        cutoff = time.time() - self._STALE_TEMP_SECONDS
        for entry in entries:
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry)
            except OSError:
                continue

    def _remove_runtime_dir(self) -> None:
        try:
            if self._runtime_dir.exists():
                shutil.rmtree(self._runtime_dir)
        except OSError as exc:
            logger.info(f'Remote edit temp cleanup deferred: path={self._runtime_dir}, error={exc}')

    @staticmethod
    def _local_signature(path: str) -> FileSignature:
        item_stat = os.stat(path)
        return int(item_stat.st_size), int(item_stat.st_mtime_ns)

    @staticmethod
    def _remote_signature(attrs) -> FileSignature:
        return int(attrs.size or 0), int(float(attrs.mtime or 0) * 1_000_000_000)

    @staticmethod
    def _set_local_mtime(
        path: str,
        remote_signature: FileSignature,
        tab_id: str,
    ) -> None:
        remote_mtime_ns = remote_signature[1]
        if remote_mtime_ns <= 0:
            return
        try:
            os.utime(path, ns=(remote_mtime_ns, remote_mtime_ns))
        except OSError as exc:
            logger.warning(
                f'Remote edit local mtime update failed: tab_id={tab_id}, '
                f'path={path}, error={exc}'
            )

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(max(0, size))
        for unit in ('B', 'KiB', 'MiB', 'GiB'):
            if value < 1024.0 or unit == 'GiB':
                return f'{value:.1f} {unit}'
            value /= 1024.0
        return f'{value:.1f} GiB'
