#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from models.favorite_path import FavoritePath, favorite_paths_from_raw, favorite_paths_to_raw

AUTH_PASSWORD = 'password'
AUTH_PUBLIC_KEY = 'publickey'


@dataclass
class SessionItem:
    """A node in the session tree.

    A folder has ``children`` but no ``host`` and no ``ssh_config_path``.
    A session leaf has connection fields but no ``children``.
    When ``ssh_config_path`` is set, ``host`` is the OpenSSH Host alias.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ''
    host: str = ''
    port: int = 22
    username: str = ''
    auth_type: str = AUTH_PASSWORD
    key_path: str = ''
    ssh_config_path: str = ''
    local_path: str = ''
    remote_path: str = ''
    info: str = ''
    local_favorites: list[FavoritePath] = field(default_factory=list)
    remote_favorites: list[FavoritePath] = field(default_factory=list)
    children: list['SessionItem'] = field(default_factory=list)
    expanded: bool = True

    def is_folder(self) -> bool:
        return not self.host and not self.ssh_config_path

    def uses_ssh_config(self) -> bool:
        return bool(self.ssh_config_path.strip())

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {'id': self.id, 'name': self.name}
        if self.host or self.ssh_config_path:
            d['host'] = self.host
            if self.ssh_config_path:
                d['ssh_config_path'] = self.ssh_config_path
            else:
                d['port'] = self.port
                d['username'] = self.username
                d['auth_type'] = self.auth_type
                if self.key_path:
                    d['key_path'] = self.key_path
            if self.local_path:
                d['local_path'] = self.local_path
            if self.remote_path:
                d['remote_path'] = self.remote_path
            if self.info:
                d['info'] = self.info
            if self.local_favorites:
                d['local_favorites'] = favorite_paths_to_raw(self.local_favorites)
            if self.remote_favorites:
                d['remote_favorites'] = favorite_paths_to_raw(self.remote_favorites)
        if self.children:
            d['children'] = [c.to_dict() for c in self.children]
        if self.is_folder() and not self.expanded:
            d['expanded'] = False
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SessionItem':
        host = str(data.get('host', '') or '')
        ssh_config_path = str(data.get('ssh_config_path', '') or '')
        return cls(
            id=data.get('id', str(uuid.uuid4())),
            name=data.get('name', ''),
            host=host,
            port=int(data.get('port', 22) or 22),
            username=str(data.get('username', '') or ''),
            auth_type=str(data.get('auth_type', AUTH_PASSWORD) or AUTH_PASSWORD),
            key_path=str(data.get('key_path', '') or ''),
            ssh_config_path=ssh_config_path,
            local_path=str(data.get('local_path', '') or ''),
            remote_path=str(data.get('remote_path', '') or ''),
            info=str(data.get('info', '') or ''),
            local_favorites=favorite_paths_from_raw(data.get('local_favorites')),
            remote_favorites=favorite_paths_from_raw(data.get('remote_favorites')),
            children=[cls.from_dict(c) for c in data.get('children', [])],
            expanded=data.get('expanded') is not False,
        )
