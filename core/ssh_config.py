#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from models.session_item import SessionItem

_KEYWORD_PATTERN = re.compile(r'^(?P<key>[A-Za-z][A-Za-z0-9]*)(?:\s*=\s*|\s+)(?P<value>.+)$')
_WILDCARD_PATTERN = re.compile(r'[*?]')


class SshConfigError(RuntimeError):
    """User-visible SSH config failure; ``message_key`` is an i18n key."""

    def __init__(self, message_key: str, **params: object) -> None:
        super().__init__(message_key)
        self.message_key = message_key
        self.params = params


@dataclass(frozen=True)
class SshConfigHost:
    """One selectable OpenSSH ``Host`` alias after parsing a config file."""

    alias: str
    hostname: str = ''
    port: int = 22
    username: str = ''
    identity_files: tuple[str, ...] = ()
    proxy_jump: str = ''

    @property
    def connect_host(self) -> str:
        return self.hostname or self.alias


def parse_ssh_config_file(path: str | os.PathLike[str]) -> dict[str, SshConfigHost]:
    """Parse a subset of OpenSSH client config: Host / HostName / User / Port / IdentityFile / ProxyJump."""
    config_path = Path(path).expanduser()
    try:
        text = config_path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        raise SshConfigError(
            'ssh_config.file_unreadable',
            path=str(config_path),
            error=str(exc),
        ) from exc

    hosts: dict[str, SshConfigHost] = {}
    current_aliases: list[str] = []
    current: dict[str, object] = {}

    def _flush() -> None:
        nonlocal current_aliases, current
        if current_aliases:
            identity = tuple(current.get('identity_files', ()))  # type: ignore[arg-type]
            port = int(current.get('port', 22) or 22)
            hostname = str(current.get('hostname', '') or '')
            username = str(current.get('username', '') or '')
            proxy_jump = str(current.get('proxy_jump', '') or '')
            for alias in current_aliases:
                if alias in hosts:
                    continue
                hosts[alias] = SshConfigHost(
                    alias=alias,
                    hostname=hostname,
                    port=port,
                    username=username,
                    identity_files=identity,
                    proxy_jump=proxy_jump,
                )
        current_aliases = []
        current = {}

    try:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            comment_at = _unquoted_hash_index(line)
            if comment_at >= 0:
                line = line[:comment_at].rstrip()
                if not line:
                    continue
            matched = _KEYWORD_PATTERN.match(line)
            if matched is None:
                raise SshConfigError('ssh_config.parse_failed', path=str(config_path), error=line)
            key = matched.group('key').lower()
            value = matched.group('value').strip()
            if key == 'match':
                # 暂不支持条件块；结束当前 Host，并忽略到下一个 Host。
                _flush()
                continue
            if key == 'host':
                _flush()
                current_aliases = [
                    alias for alias in value.split()
                    if alias and _WILDCARD_PATTERN.search(alias) is None
                ]
                continue
            if not current_aliases:
                continue
            if key == 'hostname':
                current['hostname'] = _unquote(value)
            elif key == 'user':
                current['username'] = _unquote(value)
            elif key == 'port':
                current['port'] = _parse_port(_unquote(value), config_path)
            elif key == 'identityfile':
                files = list(current.get('identity_files', []))  # type: ignore[arg-type]
                files.append(_expand_identity_file(_unquote(value)))
                current['identity_files'] = files
            elif key == 'proxyjump':
                current['proxy_jump'] = _unquote(value)
        _flush()
    except SshConfigError:
        raise
    except ValueError as exc:
        raise SshConfigError('ssh_config.parse_failed', path=str(config_path), error=str(exc)) from exc

    return hosts


def resolve_proxy_chain(
    hosts: Mapping[str, SshConfigHost],
    alias: str,
    *,
    path: str = '',
) -> list[SshConfigHost]:
    """Expand ``ProxyJump`` aliases into connect hops, destination last."""
    token = (alias or '').strip()
    if not token:
        raise SshConfigError('ssh_config.host_not_found', alias=alias, path=path)
    return _expand_jump_token(hosts, token, frozenset(), path=path)


def resolve_session_hops(session_item: SessionItem) -> list[SshConfigHost]:
    """Resolve the hop chain for a Session that points at an SSH config file."""
    path = session_item.ssh_config_path.strip()
    alias = session_item.host.strip()
    if not path:
        raise SshConfigError('ssh_config.file_unreadable', path=path, error='')
    hosts = parse_ssh_config_file(path)
    if alias not in hosts:
        raise SshConfigError('ssh_config.host_not_found', alias=alias, path=path)
    return resolve_proxy_chain(hosts, alias, path=path)


def session_host_key_endpoints(session_item: SessionItem) -> list[tuple[str, int]]:
    """Return (host, port) identities stored by TOFU for this Session."""
    if session_item.uses_ssh_config():
        hops = resolve_session_hops(session_item)
        return [(hop.connect_host, hop.port) for hop in hops]
    host = session_item.host.strip()
    if not host:
        return []
    return [(host, int(session_item.port or 22))]


def _expand_jump_token(
    hosts: Mapping[str, SshConfigHost],
    token: str,
    seen: frozenset[str],
    *,
    path: str,
) -> list[SshConfigHost]:
    username_override, hostport = _split_user_host(token.strip())
    host, port_override = _split_host_port(hostport)
    if host in hosts:
        if host in seen:
            raise SshConfigError('ssh_config.proxy_cycle', alias=host, path=path)
        entry = hosts[host]
        hops: list[SshConfigHost] = []
        if entry.proxy_jump:
            for part in _split_proxy_jump(entry.proxy_jump):
                hops.extend(_expand_jump_token(hosts, part, seen | {host}, path=path))
        hops.append(replace(
            entry,
            username=username_override or entry.username,
            port=entry.port if port_override is None else port_override,
            proxy_jump='',
        ))
        return hops
    if not host:
        raise SshConfigError('ssh_config.host_not_found', alias=token, path=path)
    return [SshConfigHost(
        alias=token.strip(),
        hostname=host,
        port=22 if port_override is None else port_override,
        username=username_override,
        identity_files=(),
        proxy_jump='',
    )]


def _split_proxy_jump(value: str) -> list[str]:
    if value.strip().lower() == 'none':
        return []
    return [part.strip() for part in value.split(',') if part.strip()]


def _split_user_host(token: str) -> tuple[str, str]:
    if '@' in token:
        username, hostport = token.rsplit('@', 1)
        return username, hostport
    return '', token


def _split_host_port(hostport: str) -> tuple[str, int | None]:
    text = hostport.strip()
    if text.startswith('[') and ']' in text:
        end = text.index(']')
        host = text[1:end]
        rest = text[end + 1:]
        if rest.startswith(':'):
            return host, _parse_port_value(rest[1:])
        return host, None
    if text.count(':') == 1:
        host, port_text = text.rsplit(':', 1)
        if port_text.isdigit():
            return host, _parse_port_value(port_text)
    return text, None


def _parse_port(value: str, config_path: Path) -> int:
    try:
        return _parse_port_value(value)
    except ValueError as exc:
        raise SshConfigError('ssh_config.parse_failed', path=str(config_path), error=str(exc)) from exc


def _parse_port_value(value: str) -> int:
    port = int(value)
    if port < 1 or port > 65535:
        raise ValueError(f'invalid port {value}')
    return port


def _expand_identity_file(raw: str) -> str:
    text = (raw or '').strip()
    if not text:
        return ''
    expanded = os.path.expanduser(text)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    return os.path.normpath(os.path.join(os.path.expanduser('~/.ssh'), expanded))


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _unquoted_hash_index(line: str) -> int:
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == '#' and not in_single and not in_double:
            return index
    return -1
