# -*- coding: utf-8 -*-
"""同一 Tab 内按方向串行执行的传输队列。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Callable, Coroutine, Literal
from uuid import uuid4

TransferKind = Literal['upload', 'download']


@dataclass(frozen=True)
class TransferJob:
    id: str
    kind: TransferKind
    paths: tuple[str, ...]
    destination: str
    state: Literal['queued', 'running', 'cancelling'] = 'queued'
    calculating: bool = True
    transferred: int = 0
    total: int = 0
    speed: str = ''


class TransferQueue:
    """只暴露不可变快照；取消始终通过 ID 查询当前状态。"""

    def __init__(
        self,
        run: Callable[[TransferJob], Coroutine[Any, Any, None]],
        track: Callable[[asyncio.Task], None],
        changed: Callable[[], None],
    ) -> None:
        self._run = run
        self._track = track
        self._changed = changed
        self._jobs: dict[str, TransferJob] = {}
        self._active: dict[TransferKind, tuple[str, asyncio.Task]] = {}

    def jobs(self, kind: TransferKind) -> list[TransferJob]:
        return [job for job in self._jobs.values() if job.kind == kind]

    def enqueue(self, kind: TransferKind, paths: list[str], destination: str) -> None:
        if not paths:
            return
        job = TransferJob(uuid4().hex, kind, tuple(paths), destination)
        self._jobs[job.id] = job
        self._start_next(kind)
        self._changed()

    def _start_next(self, kind: TransferKind) -> None:
        if kind in self._active:
            return
        job = next((job for job in self.jobs(kind) if job.state == 'queued'), None)
        if job is None:
            return
        job = replace(job, state='running')
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job))
        # 在任何 await 或信号通知之前关联任务，覆盖“刚轮到执行便取消”。
        self._active[kind] = (job.id, task)
        self._track(task)
        task.add_done_callback(lambda _done: self._finished(job))

    def _finished(self, job: TransferJob) -> None:
        self._jobs.pop(job.id, None)
        self._active.pop(job.kind, None)
        # 只有旧任务的 finally 全部完成后才允许下一项开始。
        self._start_next(job.kind)
        self._changed()

    def cancel(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None or job.state == 'cancelling':
            return
        if job.state == 'queued':
            del self._jobs[job_id]
        else:
            self._jobs[job_id] = replace(job, state='cancelling')
            self._active[job.kind][1].cancel()
        self._changed()

    def cancel_all(self) -> set[asyncio.Task]:
        """取消全部任务，返回仍需等待清理的 Task。"""
        # 先一次性移除等待项，防止活动任务取消后的回调再启动它们。
        self._jobs = {key: job for key, job in self._jobs.items() if job.state != 'queued'}
        for job_id in list(self._jobs):
            self.cancel(job_id)
        self._changed()
        return {task for _job_id, task in self._active.values()}

    def update_progress(
        self,
        kind: TransferKind,
        speed: str,
        transferred: int,
        total: int,
        calculating: bool,
    ) -> None:
        active = self._active.get(kind)
        if active is None:
            return
        job_id, _task = active
        self._jobs[job_id] = replace(
            self._jobs[job_id], speed=speed, transferred=transferred,
            total=total, calculating=calculating,
        )
        self._changed()
