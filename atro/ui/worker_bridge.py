"""Thread-safe bridge connecting PyQt6 UI thread with asyncio AsyncJobWorker."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from atro.config import AppSettings
from atro.core.composer_schema import ClipComposerConfig
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies_async
from atro.core.schemas import (
    AnalysisResult,
    DownloadResult,
    JobRequest,
    JobState,
    PreflightReport,
    TranscriptResult,
)
from atro.core.subprocess_utils import run_subprocess_capture
from atro.modules.analyzer import AnalyzerService
from atro.modules.cutter.service import CutterService
from atro.pipeline.state_store import load_state, save_state
from atro.pipeline.worker import AsyncJobWorker


class WorkerBridge(QObject):
    """Runs an asyncio loop in a background daemon thread and emits Qt signals."""

    preflight_ready = pyqtSignal(object)
    job_updated = pyqtSignal(object)
    log_received = pyqtSignal(str, str)
    bridge_error = pyqtSignal(str)
    custom_clip_rendered = pyqtSignal(str, object)
    batch_render_progress = pyqtSignal(str, int, int, str)
    batch_clips_rendered = pyqtSignal(str, object)
    standalone_clip_rendered = pyqtSignal(object)
    alternative_clips_generated = pyqtSignal(str, int)

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

    def render_custom_clip(self, job_id: str, config: ClipComposerConfig) -> None:
        if self._loop is None:
            return

        async def _render() -> None:
            try:
                settings = self._settings_provider()
                preflight = self.get_preflight()
                job_dir = Path(settings.workspace_dir) / job_id
                state = load_state(job_dir)
                logger = JobLogger(job_id, Path(state.log_path), on_log=self._handle_log)

                meta_path = (
                    Path(state.artifacts.metadata_json)
                    if state.artifacts.metadata_json
                    else job_dir / "metadata.json"
                )
                download = DownloadResult.model_validate_json(meta_path.read_text(encoding="utf-8"))

                tr_path = (
                    Path(state.artifacts.transcript_json)
                    if state.artifacts.transcript_json
                    else job_dir / "transcript.json"
                )
                segs = []
                if tr_path.is_file():
                    segs = TranscriptResult.model_validate_json(
                        tr_path.read_text(encoding="utf-8")
                    ).segments

                vir_score, reason = 8, "Custom Composer Render"
                an_path = (
                    Path(state.artifacts.analysis_json)
                    if state.artifacts.analysis_json
                    else job_dir / "analysis.json"
                )
                if an_path.is_file():
                    an = AnalysisResult.model_validate_json(an_path.read_text(encoding="utf-8"))
                    if 1 <= config.clip_index <= len(an.clips):
                        vir_score = an.clips[config.clip_index - 1].virality_score
                        reason = an.clips[config.clip_index - 1].reason

                rendered = await CutterService().render_clip_with_composer_config(
                    job_id=job_id,
                    download=download,
                    config=config,
                    job_dir=job_dir,
                    preflight=preflight,
                    logger=logger,
                    transcript_segments=segs,
                    settings=settings,
                    virality_score=vir_score,
                    reason=reason,
                    update_render_result=True,
                )
                self.custom_clip_rendered.emit(job_id, rendered)
            except Exception as exc:
                self.bridge_error.emit(f"Gagal merender klip Studio: {exc}")

        asyncio.run_coroutine_threadsafe(_render(), self._loop)

    def render_batch_custom_clips(
        self,
        job_id: str,
        clip_indices: list[int],
        style_template_config: Optional[ClipComposerConfig] = None,
        force_redetect_faces: bool = True,
    ) -> None:
        if self._loop is None:
            return

        indices_copy = list(clip_indices)
        template_copy = (
            style_template_config.model_copy(deep=True)
            if style_template_config is not None
            else None
        )

        async def _render_batch() -> None:
            try:
                settings = self._settings_provider()
                preflight = self.get_preflight()
                job_dir = Path(settings.workspace_dir) / job_id
                state = load_state(job_dir)
                logger = JobLogger(job_id, Path(state.log_path), on_log=self._handle_log)

                meta_path = (
                    Path(state.artifacts.metadata_json)
                    if state.artifacts.metadata_json
                    else job_dir / "metadata.json"
                )
                download = DownloadResult.model_validate_json(meta_path.read_text(encoding="utf-8"))

                tr_path = (
                    Path(state.artifacts.transcript_json)
                    if state.artifacts.transcript_json
                    else job_dir / "transcript.json"
                )
                segs = []
                if tr_path.is_file():
                    segs = TranscriptResult.model_validate_json(
                        tr_path.read_text(encoding="utf-8")
                    ).segments

                an_path = (
                    Path(state.artifacts.analysis_json)
                    if state.artifacts.analysis_json
                    else job_dir / "analysis.json"
                )
                if not an_path.is_file():
                    raise FileNotFoundError(f"Hasil analisis klip tidak ditemukan: {an_path}")
                analysis = AnalysisResult.model_validate_json(an_path.read_text(encoding="utf-8"))

                def _on_prog(done_idx: int, total_cnt: int, title: str) -> None:
                    self.batch_render_progress.emit(job_id, done_idx, total_cnt, title)

                rendered_list = await CutterService().render_batch_clips_with_composer(
                    job_id=job_id,
                    download=download,
                    analysis=analysis,
                    job_dir=job_dir,
                    preflight=preflight,
                    logger=logger,
                    clip_indices=indices_copy,
                    transcript_segments=segs,
                    settings=settings,
                    style_template_config=template_copy,
                    force_redetect_faces=force_redetect_faces,
                    on_clip_progress=_on_prog,
                    request=state.request,
                )
                self.batch_clips_rendered.emit(job_id, rendered_list)
            except Exception as exc:
                self.bridge_error.emit(f"Gagal merender batch klip: {exc}")

        asyncio.run_coroutine_threadsafe(_render_batch(), self._loop)

    def generate_alternative_clips(self, job_id: str) -> None:
        """Curate & render additional non-overlapping alternative clips without re-transcribing."""
        if self._loop is None:
            return

        async def _alt() -> None:
            try:
                settings = self._settings_provider()
                preflight = self.get_preflight()
                job_dir = Path(settings.workspace_dir) / job_id
                state = load_state(job_dir)
                logger = JobLogger(job_id, Path(state.log_path), on_log=self._handle_log)

                meta_path = (
                    Path(state.artifacts.metadata_json)
                    if state.artifacts.metadata_json
                    else job_dir / "metadata.json"
                )
                download = DownloadResult.model_validate_json(meta_path.read_text(encoding="utf-8"))

                tr_path = (
                    Path(state.artifacts.transcript_json)
                    if state.artifacts.transcript_json
                    else job_dir / "transcript.json"
                )
                if not tr_path.is_file():
                    raise FileNotFoundError(f"Transkrip belum tersedia untuk {job_id}.")
                transcript = TranscriptResult.model_validate_json(tr_path.read_text(encoding="utf-8"))

                an_path = (
                    Path(state.artifacts.analysis_json)
                    if state.artifacts.analysis_json
                    else job_dir / "analysis.json"
                )
                old_an = (
                    AnalysisResult.model_validate_json(an_path.read_text(encoding="utf-8"))
                    if an_path.is_file()
                    else None
                )
                old_clips = list(old_an.clips) if old_an is not None else []
                old_topics = list(old_an.topic_outline) if old_an is not None else []
                excluded = list(state.request.excluded_time_spans) + [
                    [round(c.start_time, 2), round(c.end_time, 2)] for c in old_clips
                ]

                logger.info(
                    "analyzer",
                    f"Mencari klip alternatif baru (menghindari {len(old_clips)} klip lama)...",
                )
                alt_req = state.request.model_copy(update={"excluded_time_spans": excluded})
                fresh_an = await AnalyzerService().run(
                    job_id=job_id,
                    download=download,
                    transcript=transcript,
                    request=alt_req,
                    job_dir=job_dir,
                    settings=settings,
                    logger=logger,
                )

                combined_clips = old_clips + list(fresh_an.clips)
                combined_topics = old_topics or list(fresh_an.topic_outline)
                merged_an = AnalysisResult(
                    job_id=job_id,
                    model=fresh_an.model,
                    platform_target=state.request.platform_target,
                    clips=combined_clips,
                    topic_outline=combined_topics,
                    validation_warnings=list(fresh_an.validation_warnings),
                )
                an_path.write_text(merged_an.model_dump_json(indent=2), encoding="utf-8")

                new_indices = list(range(len(old_clips) + 1, len(combined_clips) + 1))

                def _on_prog(done_idx: int, total_cnt: int, title: str) -> None:
                    self.batch_render_progress.emit(job_id, done_idx, total_cnt, title)

                await CutterService().render_batch_clips_with_composer(
                    job_id=job_id,
                    download=download,
                    analysis=merged_an,
                    job_dir=job_dir,
                    preflight=preflight,
                    logger=logger,
                    clip_indices=new_indices,
                    transcript_segments=transcript.segments,
                    settings=settings,
                    force_redetect_faces=True,
                    on_clip_progress=_on_prog,
                    request=state.request,
                )
                state.request = alt_req
                state.artifacts.analysis_json = str(an_path.resolve())
                rr_path = job_dir / "render_result.json"
                if rr_path.is_file():
                    state.artifacts.render_result_json = str(rr_path.resolve())
                save_state(state)
                self.job_updated.emit(state.model_copy(deep=True))
                self.alternative_clips_generated.emit(job_id, len(new_indices))
            except Exception as exc:
                self.bridge_error.emit(f"Gagal membuat klip alternatif: {exc}")

        asyncio.run_coroutine_threadsafe(_alt(), self._loop)

    def render_standalone_clip(self, source_video_path: str, config: ClipComposerConfig) -> None:
        if self._loop is None:
            return

        async def _render() -> None:
            try:
                settings = self._settings_provider()
                preflight = self.get_preflight()
                src = Path(source_video_path)
                job_id = f"studio_{src.stem[:24]}"
                job_dir = Path(settings.workspace_dir) / "studio_exports" / src.stem[:32]
                job_dir.mkdir(parents=True, exist_ok=True)
                logger = JobLogger(job_id, job_dir / "studio.log", on_log=self._handle_log)

                width, height, fps, duration, has_audio = 1920, 1080, 30.0, 60.0, True
                if preflight.ffprobe.available and preflight.ffprobe.path:
                    rc, out, _ = await run_subprocess_capture(
                        [
                            preflight.ffprobe.path,
                            "-v",
                            "quiet",
                            "-print_format",
                            "json",
                            "-show_format",
                            "-show_streams",
                            str(src),
                        ]
                    )
                    if rc == 0 and out.strip():
                        info = json.loads(out)
                        streams = info.get("streams", [])
                        vs = next((s for s in streams if s.get("codec_type") == "video"), None)
                        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
                        has_audio = a_stream is not None
                        if vs:
                            width = int(vs.get("width") or width)
                            height = int(vs.get("height") or height)
                            rate = str(vs.get("r_frame_rate") or "30/1")
                            if "/" in rate:
                                n, d = rate.split("/", 1)
                                fps = float(n) / max(1.0, float(d))
                        fmt_dur = info.get("format", {}).get("duration")
                        if fmt_dur:
                            duration = max(1.0, float(fmt_dur))

                download = DownloadResult(
                    job_id=job_id,
                    url="",
                    video_id=src.stem,
                    title=src.stem,
                    channel="Local Studio",
                    duration_sec=duration,
                    width=width,
                    height=height,
                    fps=fps,
                    has_audio=has_audio,
                    source_video_path=str(src.resolve()),
                )
                rendered = await CutterService().render_clip_with_composer_config(
                    job_id=job_id,
                    download=download,
                    config=config,
                    job_dir=job_dir,
                    preflight=preflight,
                    logger=logger,
                    transcript_segments=[],
                    settings=settings,
                    update_render_result=False,
                )
                self.standalone_clip_rendered.emit(rendered)
            except Exception as exc:
                self.bridge_error.emit(f"Gagal export video Studio: {exc}")

        asyncio.run_coroutine_threadsafe(_render(), self._loop)

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
