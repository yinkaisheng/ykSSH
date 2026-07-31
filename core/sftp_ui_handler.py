#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
from typing import Callable, List, Optional

from PyQt5.QtCore import QObject

from core.sftp_service import delete_path, download, mkdir, rename, upload
from core.connection_manager import ConnectionManager
from log_util import logger
from ui.dialog_i18n import message_warning


class SftpUiHandler(QObject):
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

    def set_local_dir(self, path: str) -> None:
        self._local_dir = path

    def set_remote_dir(self, path: str) -> None:
        self._remote_dir = path or '/'

    def refresh_remote(self, path: str) -> None:
        self._remote_dir = path or '/'
        asyncio.create_task(self._cm.refresh_remote_list(self.tab_id, self._remote_dir))

    def upload_local_paths(self, local_paths: List[str]) -> None:
        asyncio.create_task(self._upload_async(local_paths))

    async def _upload_async(self, local_paths: List[str]) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        remote_dir = self._remote_dir.rstrip('/') or '/'
        for local in local_paths:
            name = os.path.basename(local.rstrip(os.sep))
            remote = f'{remote_dir}/{name}' if remote_dir != '/' else f'/{name}'
            try:
                await upload(sftp, local, remote)
            except Exception as exc:
                logger.warning(f'upload failed: {exc}')
                message_warning(None, '', str(exc))
        self._cm.invalidate_remote_cache(self.tab_id, self._remote_dir)
        await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
        self._on_refresh_ui()

    def download_remote_paths(self, remote_paths: List[str]) -> None:
        asyncio.create_task(self._download_async(remote_paths))

    async def _download_async(self, remote_paths: List[str]) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        for remote in remote_paths:
            name = os.path.basename(remote.rstrip('/'))
            local = os.path.join(self._local_dir, name)
            try:
                await download(sftp, remote, local)
            except Exception as exc:
                logger.warning(f'download failed: {exc}')
                message_warning(None, '', str(exc))
        self._on_refresh_ui()

    def delete_remote_paths(self, remote_paths: List[str]) -> None:
        asyncio.create_task(self._delete_async(remote_paths))

    async def _delete_async(self, remote_paths: List[str]) -> None:
        ssh = self._cm.get_session(self.tab_id)
        if ssh is None:
            return
        sftp = ssh.get_sftp()
        if sftp is None:
            return
        for remote in remote_paths:
            try:
                await delete_path(sftp, remote)
            except Exception as exc:
                logger.warning(f'delete failed: {exc}')
                message_warning(None, '', str(exc))
        self._cm.invalidate_remote_cache(self.tab_id, self._remote_dir)
        await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
        self._on_refresh_ui()

    def rename_remote(self, old_name: str, new_name: str) -> None:
        asyncio.create_task(self._rename_async(old_name, new_name))

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
        except Exception as exc:
            message_warning(None, '', str(exc))
            return
        self._cm.invalidate_remote_cache(self.tab_id, self._remote_dir)
        await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
        self._on_refresh_ui()

    def mkdir_remote(self, name: str) -> None:
        asyncio.create_task(self._mkdir_async(name))

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
        except Exception as exc:
            message_warning(None, '', str(exc))
            return
        self._cm.invalidate_remote_cache(self.tab_id, self._remote_dir)
        await self._cm.refresh_remote_list(self.tab_id, self._remote_dir)
        self._on_refresh_ui()
