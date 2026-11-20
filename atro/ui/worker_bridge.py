"""Thread-safe bridge connecting PyQt6 UI thread with asyncio AsyncJobWorker."""

from __future__ import annotations

import asyncio
import threading
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from atro.config import AppSettings
from atro.core.preflight import check_startup_dependencies_async
from atro.core.schemas import JobRequest, JobState, PreflightReport
from atro.pipeline.worker import AsyncJobWorker


class WorkerBridge(QObject):
    """Runs an asyncio loop in a background daemon thread and emits Qt signals."""

    preflight_ready = pyqtSignal(object)
    job_updated = pyqtSignal(object)
    log_received = pyqtSignal(str, str)
    bridge_error = pyqtSignal(str)

    def __init__(
        self,
        settings_provider: Callable[[], AppSettings],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._preflight: Optional[PreflightReport] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._worker: Optional[AsyncJobWorker] = None

    def get_preflight(self) -> PreflightReport:
        if self._preflight is not None:
            return self._preflight
        from atro.core.preflight import check_startup_dependencies

        self._preflight = check_startup_dependencies(self._settings_provider())
        return self._preflight

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="atro-asyncio-thread", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=5.0)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        self._worker = AsyncJobWorker(
            settings_provider=self._settings_provider,
            preflight_provider=self.get_preflight,
            on_state_change=self._handle_state_change,
            on_log=self._handle_log,
        )

        def _on_loop_started() -> None:
            assert self._worker is not None
            self._worker.start()
            self._ready_event.set()

        self._loop.call_soon(_on_loop_started)

        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def _handle_state_change(self, state: JobState) -> None:
        self.job_updated.emit(state)

    def _handle_log(self, job_id: str, line: str) -> None:
        self.log_received.emit(job_id, line)

    def refresh_preflight(self) -> None:
        if self._loop is None:
            return

        async def _check() -> None:
            report = await check_startup_dependencies_async(self._settings_provider())
            self._preflight = report
            self.preflight_ready.emit(report)

        asyncio.run_coroutine_threadsafe(_check(), self._loop)

    def submit_request(self, request: JobRequest) -> None:
        if self._loop is None or self._worker is None:
            return

        async def _submit() -> None:
            try:
                assert self._worker is not None
                await self._worker.submit_job(request)
            except Exception as exc:
                self.bridge_error.emit(f"Gagal memulai job: {exc}")

        asyncio.run_coroutine_threadsafe(_submit(), self._loop)

    def resume_job(self, job_id: str) -> None:
        if self._loop is None or self._worker is None:
            return

        async def _resume() -> None:
            try:
                assert self._worker is not None
                await self._worker.resume_job(job_id)
            except Exception as exc:
                self.bridge_error.emit(f"Gagal me-resume job {job_id}: {exc}")

        asyncio.run_coroutine_threadsafe(_resume(), self._loop)

    def cancel_job(self, job_id: str) -> None:
        if self._loop is None or self._worker is None:
            return

        async def _cancel() -> None:
            assert self._worker is not None
            self._worker.cancel_job(job_id)

        asyncio.run_coroutine_threadsafe(_cancel(), self._loop)

    def shutdown(self) -> None:
        if self._loop is not None and self._loop.is_running():
            if self._worker is not None:
                fut = asyncio.run_coroutine_threadsafe(self._worker.stop(), self._loop)
                try:
                    fut.result(timeout=3.0)
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)
