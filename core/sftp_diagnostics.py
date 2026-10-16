#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远端目录操作的阈值计时与跨异步任务请求关联。"""
from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import count
from types import TracebackType
from typing import Iterator

from log_util import logger


_SLOW_SECONDS = 0.5
_PROGRESS_SECONDS = 5.0
_REQUEST_IDS = count(1)


@dataclass(frozen=True)
class _NavigationRequest:
    tab_id: str
    path: str
    source: str
    started: float
    request_id: int


_NAVIGATION: ContextVar[_NavigationRequest | None] = ContextVar('remote_navigation', default=None)


@contextmanager
def remote_navigation_context(tab_id: str, path: str, source: str) -> Iterator[None]:
    """从点击/提交时开始计时；create_task 自动继承请求上下文。"""
    current = _NAVIGATION.get()
    if current is not None and current.tab_id == tab_id:
        yield
        return
    request = _NavigationRequest(tab_id, path, source, time.perf_counter(), next(_REQUEST_IDS))
    token = _NAVIGATION.set(request)
    try:
        yield
    finally:
        _NAVIGATION.reset(token)


class SftpTiming:
    """仅慢操作输出等待日志和汇总；重复阶段累计耗时并记录最慢路径。"""

    def __init__(self, operation: str, tab_id: str, path: str) -> None:
        self.operation = operation
        self.tab_id = tab_id
        self.path = path
        self.started = time.perf_counter()
        request = _NAVIGATION.get()
        self.request = request if request is not None and request.tab_id == tab_id else None
        self.metrics: dict[str, int | float | str] = {}
        self.phase = 'start'
        self.phase_path = path
        self.phase_started = self.started
        self._measure_depth = 0
        self._watchdog: asyncio.TimerHandle | None = None

    def __enter__(self) -> SftpTiming:
        request_start = self.request.started if self.request is not None else self.started
        delay = max(0.001, _SLOW_SECONDS - (time.perf_counter() - request_start))
        self._watchdog = asyncio.get_running_loop().call_later(delay, self._report_waiting)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
        elapsed = time.perf_counter() - self.started
        request_elapsed = time.perf_counter() - self.request.started if self.request is not None else elapsed
        if max(elapsed, request_elapsed) > _SLOW_SECONDS:
            status = 'done'
            if isinstance(exc, asyncio.CancelledError):
                status = 'cancelled'
            elif exc is not None:
                status = 'failed'
            if exc_type is not None:
                self.metrics['error_type'] = exc_type.__name__
            self._report(status)

    @contextmanager
    def measure(self, phase: str, path: str | None = None) -> Iterator[None]:
        started = time.perf_counter()
        previous = self.phase, self.phase_path, self.phase_started
        phase_path = path or self.path
        self.phase, self.phase_path, self.phase_started = phase, phase_path, started
        self._measure_depth += 1
        count_key = f'{phase}_count'
        self.metrics[count_key] = int(self.metrics.get(count_key, 0)) + 1
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            total_key, max_key = f'{phase}_ms', f'{phase}_max_ms'
            self.metrics[total_key] = float(self.metrics.get(total_key, 0.0)) + elapsed_ms
            if elapsed_ms >= float(self.metrics.get(max_key, 0.0)):
                self.metrics[max_key] = elapsed_ms
                self.metrics[f'{phase}_slowest_path'] = phase_path
            self._measure_depth -= 1
            if self._measure_depth:
                # 子阶段结束后恢复外层阶段，避免把网络等待误标为 UI 耗时。
                self.phase, self.phase_path, self.phase_started = previous

    def _report_waiting(self) -> None:
        # Qt 定时器可能提前唤醒，必须用单调时钟再次核对 500ms 阈值。
        started = self.request.started if self.request is not None else self.started
        remaining = _SLOW_SECONDS - (time.perf_counter() - started)
        if remaining >= 0:
            self._watchdog = asyncio.get_running_loop().call_later(max(0.001, remaining), self._report_waiting)
            return
        self._report('waiting')
        self._watchdog = asyncio.get_running_loop().call_later(_PROGRESS_SECONDS, self._report_waiting)

    def _report(self, status: str) -> None:
        now = time.perf_counter()
        fields = [
            '[SFTP-SLOW]', f'operation={self.operation}', f'tab_id={self.tab_id}',
            f'path={self.path!r}', f'status={status}', f'elapsed_ms={(now - self.started) * 1000:.1f}',
            f'phase={self.phase}', f'phase_path={self.phase_path!r}',
            f'phase_elapsed_ms={(now - self.phase_started) * 1000:.1f}',
        ]
        if self.request is not None:
            fields.extend((
                f'request_id={self.request.request_id}', f'source={self.request.source}',
                f'requested_path={self.request.path!r}',
                f'request_elapsed_ms={(now - self.request.started) * 1000:.1f}',
            ))
        fields.extend(
            f'{key}={value:.1f}' if isinstance(value, float) else f'{key}={value!r}'
            for key, value in self.metrics.items()
        )
        logger.warning(', '.join(fields))
