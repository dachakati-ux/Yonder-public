"""Background executor for coder runs — durable, resumable, cancellable.

Runs advance outside the HTTP request path. One task per run, a per-run
lock against concurrent drivers, and a boot-time sweep that resumes runs
left ``active`` by a gateway restart.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from yonder_telemetry import log_event

from app.services.coder_runs import TERMINAL_STATUSES, CoderRunOrchestrator
from app.services.coder_workspace import CoderError


@dataclass
class CoderRunExecutor:
    orchestrator: CoderRunOrchestrator
    logger: Any | None = None
    max_concurrent: int = 2
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _cancel_requested: set[str] = field(default_factory=set)
    _semaphore: asyncio.Semaphore | None = None

    def _sem(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    def is_running(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def submit(
        self, subject: str, run_id: str, *, wait: bool = False
    ) -> dict[str, Any]:
        """Start (or join) the driver task for a run."""
        # Validate existence + ownership before spawning anything.
        run = self.orchestrator.get_run(subject, run_id)
        if run.status in TERMINAL_STATUSES:
            return {"run_id": run_id, "executor": "already_terminal", "status": run.status}
        self._cancel_requested.discard(run_id)
        if not self.is_running(run_id):
            self._tasks[run_id] = asyncio.create_task(
                self._drive(subject, run_id), name=f"coder-run-{run_id}"
            )
        if wait:
            await asyncio.shield(self._tasks[run_id])
            run = self.orchestrator.get_run(subject, run_id)
            return {"run_id": run_id, "executor": "finished", "status": run.status}
        return {"run_id": run_id, "executor": "running", "status": run.status}

    async def cancel(self, subject: str, run_id: str) -> dict[str, Any]:
        run = self.orchestrator.get_run(subject, run_id)
        if run.status in TERMINAL_STATUSES:
            return {"run_id": run_id, "status": run.status, "cancelled": False}
        self._cancel_requested.add(run_id)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            # The driver observes the flag between steps; wait for it to land.
            await asyncio.shield(task)
        else:
            # No driver active — mark the run cancelled directly.
            run.status = "cancelled"
            run.error = "cancelled"
            self.orchestrator.store.save(run)
        run = self.orchestrator.get_run(subject, run_id)
        return {"run_id": run_id, "status": run.status, "cancelled": True}

    async def resume_active_runs(self) -> list[str]:
        """Boot sweep: restart driver tasks for runs left active by a restart."""
        resumed: list[str] = []
        try:
            active = self.orchestrator.store.list_active_runs()
        except Exception as exc:  # noqa: BLE001 — never block gateway startup
            if self.logger:
                log_event(
                    self.logger,
                    "coder.executor.resume_sweep_failed",
                    level=logging.WARNING,
                    error=str(exc),
                )
            return resumed
        for run in active:
            if self.is_running(run.id):
                continue
            self._tasks[run.id] = asyncio.create_task(
                self._drive(run.principal_subject, run.id),
                name=f"coder-run-{run.id}",
            )
            resumed.append(run.id)
        if resumed and self.logger:
            log_event(
                self.logger,
                "coder.executor.resumed_runs",
                run_ids=resumed,
            )
        return resumed

    async def shutdown(self) -> None:
        for run_id, task in list(self._tasks.items()):
            if not task.done():
                self._cancel_requested.add(run_id)
                task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _drive(self, subject: str, run_id: str) -> None:
        async with self._sem():
            async with self._lock(run_id):
                try:
                    await self.orchestrator.advance(
                        subject,
                        run_id,
                        auto=True,
                        should_continue=lambda: run_id not in self._cancel_requested,
                    )
                except CoderError as exc:
                    if self.logger:
                        log_event(
                            self.logger,
                            "coder.executor.run_error",
                            level=logging.WARNING,
                            run_id=run_id,
                            code=exc.code,
                            error=exc.message,
                        )
                except Exception as exc:  # noqa: BLE001 — record, never crash loop
                    if self.logger:
                        log_event(
                            self.logger,
                            "coder.executor.run_crashed",
                            level=logging.ERROR,
                            run_id=run_id,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    try:
                        run = self.orchestrator.get_run(subject, run_id)
                        if run.status not in TERMINAL_STATUSES:
                            run.status = "failed"
                            run.error = f"executor_crash:{type(exc).__name__}: {exc}"
                            self.orchestrator.store.save(run)
                    except Exception:  # noqa: BLE001
                        pass
                finally:
                    self._cancel_requested.discard(run_id)
