"""Async background worker queue executing AutoClip jobs without blocking UI."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Optional

from atro.config import AppSettings
from atro.core.schemas import JobRequest, JobState, JobStatus, PreflightReport
from atro.pipeline.orchestrator import PipelineOrchestrator
from atro.pipeline.state_store import create_job_state, load_state, save_state


class AsyncJobWorker:
    """Asynchronous background worker queue managing sequential job execution."""

    def __init__(
        self,
        settings_provider: Callable[[], AppSettings],
        preflight_provider: Callable[[], PreflightReport],
        on_state_change: Optional[Callable[[JobState], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._settings_provider = settings_provider
        self._preflight_provider = preflight_provider
        self._on_state_change = on_state_change
        self._orchestrator = PipelineOrchestrator(
            settings_provider=settings_provider,
            preflight_provider=preflight_provider,
            on_state_change=on_state_change,
            on_log=on_log,
        )
        self._queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._running_job_id: Optional[str] = None
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    def start(self) -> None:
        """Start the background worker loop inside the active event loop."""
        if self._worker_task is None or self._worker_task.done():
            self._stopping = False
            self._worker_task = asyncio.create_task(self._worker_loop(), name="atro-job-worker")

    async def stop(self) -> None:
        """Cancel active jobs and stop background worker gracefully."""
        self._stopping = True
        for ev in self._cancel_events.values():
            ev.set()
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None

    def _notify_state(self, state: JobState) -> None:
        save_state(state)
        if self._on_state_change is not None:
            try:
                self._on_state_change(state.model_copy(deep=True))
            except Exception:
                pass

    async def submit_job(self, request: JobRequest) -> JobState:
        """Create a new job state on disk and enqueue it for background execution."""
        settings = self._settings_provider()
        state = create_job_state(settings.workspace_dir, request)
        self._cancel_events[state.job_id] = asyncio.Event()
        self._notify_state(state)
        await self._queue.put((state.job_id, False))
        return state

    async def resume_job(self, job_id: str) -> JobState:
        """Re-enqueue an existing FAILED/CANCELLED job to continue from saved artifacts."""
        settings = self._settings_provider()
        job_dir = settings.workspace_dir / job_id
        state = load_state(job_dir)
        state.status = JobStatus.QUEUED
        state.current_stage = "queued"
        state.error = None
        self._cancel_events[job_id] = asyncio.Event()
        self._notify_state(state)
        await self._queue.put((job_id, True))
        return state

    def cancel_job(self, job_id: str) -> bool:
        """Signal cancellation for a queued or running job."""
        ev = self._cancel_events.get(job_id)
        if ev is not None:
            ev.set()

        # If queued (not yet running), immediately transition state to CANCELLED
        if self._running_job_id != job_id:
            settings = self._settings_provider()
            job_dir = settings.workspace_dir / job_id
            if (job_dir / "state.json").is_file():
                state = load_state(job_dir)
                if state.status == JobStatus.QUEUED:
                    state.status = JobStatus.CANCELLED
                    state.current_stage = "cancelled"
                    state.error = "Dibatalkan sebelum dimulai."
                    self._notify_state(state)
                    return True
        return ev is not None

    async def _worker_loop(self) -> None:
        while not self._stopping:
            job_id, resume = await self._queue.get()
            try:
                settings = self._settings_provider()
                job_dir = Path(settings.workspace_dir) / job_id
                if not (job_dir / "state.json").is_file():
                    continue

                cancel_event = self._cancel_events.setdefault(job_id, asyncio.Event())
                state = load_state(job_dir)
                if cancel_event.is_set() or state.status == JobStatus.CANCELLED:
                    if state.status != JobStatus.CANCELLED:
                        state.status = JobStatus.CANCELLED
                        state.current_stage = "cancelled"
                        state.error = "Dibatalkan oleh pengguna."
                        self._notify_state(state)
                    continue

                self._running_job_id = job_id
                await self._orchestrator.run_job(
                    state=state,
                    resume=resume,
                    cancel_event=cancel_event,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Defensive guard so one unexpected error never kills the worker loop
                pass
            finally:
                if self._running_job_id == job_id:
                    self._running_job_id = None
                self._queue.task_done()
