"""End-to-end async pipeline orchestrator tying together all 4 independent modules."""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path
from typing import Callable, Optional

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.schemas import (
    AnalysisResult,
    DownloadResult,
    JobState,
    JobStatus,
    PreflightReport,
    RenderResult,
    TranscriptResult,
)
from atro.core.subprocess_utils import JobCancelledError
from atro.modules.analyzer import AnalyzerService
from atro.modules.cutter import CutterService
from atro.modules.downloader import DownloaderService
from atro.modules.transcriber import TranscriberService
from atro.modules.transcriber.proofreader import refine_transcript_result_for_clips
from atro.pipeline.state_store import save_state


class PipelineOrchestrator:
    """Coordinates Downloader -> Transcriber -> Analyzer -> Cutter with resume support."""

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
        self._on_log = on_log

        self._downloader = DownloaderService()
        self._transcriber = TranscriberService()
        self._analyzer = AnalyzerService()
        self._cutter = CutterService()

    def _emit_state(self, state: JobState) -> None:
        save_state(state)
        if self._on_state_change is not None:
            try:
                self._on_state_change(state.model_copy(deep=True))
            except Exception:
                pass

    def _update_progress(
        self,
        state: JobState,
        status: JobStatus,
        stage: str,
        pct: float,
    ) -> None:
        state.status = status
        state.current_stage = stage
        state.progress_pct = round(max(0.0, min(100.0, pct)), 1)
        state.error = None
        self._emit_state(state)

    async def run_job(
        self,
        state: JobState,
        resume: bool = False,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> JobState:
        job_dir = Path(state.workspace_dir)
        logger = JobLogger(state.job_id, Path(state.log_path), on_log=self._on_log)
        settings = self._settings_provider()
        preflight = self._preflight_provider()

        if state.request.source_type == "local_file":
            binary_ok = preflight.ffmpeg.available
            binary_err = preflight.ffmpeg.error or "Binary ffmpeg tidak tersedia."
        else:
            binary_ok = preflight.ok
            binary_err = "; ".join(preflight.errors) or "Preflight binary check gagal."

        if not binary_ok:
            logger.error("preflight", binary_err)
            state.status = JobStatus.FAILED
            state.current_stage = "preflight"
            state.error = binary_err
            self._emit_state(state)
            return state

        logger.info(
            "pipeline",
            f"Memulai {'resume ' if resume else ''}job {state.job_id} | "
            f"source={state.request.source_type} ({state.request.display_source}) | "
            f"target={state.request.clip_count} klip ({state.request.min_duration_sec}-{state.request.max_duration_sec}s, "
            f"{state.request.aspect_ratio}, pad={state.request.padding_sec}s)",
        )

        try:
            # 1. DOWNLOADER / LOCAL INGEST (0% -> 25%)
            download_result: Optional[DownloadResult] = None
            metadata_path = job_dir / "metadata.json"
            if resume and metadata_path.is_file():
                try:
                    cached = DownloadResult.model_validate_json(metadata_path.read_text(encoding="utf-8"))
                    if Path(cached.source_video_path).is_file():
                        download_result = cached
                        state.artifacts.metadata_json = str(metadata_path.resolve())
                        logger.info("downloader", "Resume: menggunakan metadata.json & source.mp4 yang sudah ada.")
                except Exception:
                    download_result = None

            if download_result is None:
                self._update_progress(state, JobStatus.DOWNLOADING, "downloader", 2.0)

                def _dl_prog(pct: float) -> None:
                    self._update_progress(state, JobStatus.DOWNLOADING, "downloader", 2.0 + pct * 0.23)

                if state.request.source_type == "local_file":
                    assert state.request.local_video_path is not None
                    sub_path = (
                        Path(state.request.local_subtitle_path)
                        if state.request.local_subtitle_path
                        else None
                    )
                    download_result = await self._downloader.ingest_local_file(
                        job_id=state.job_id,
                        local_video_path=Path(state.request.local_video_path),
                        local_subtitle_path=sub_path,
                        job_dir=job_dir,
                        preflight=preflight,
                        logger=logger,
                        on_progress=_dl_prog,
                        cancel_event=cancel_event,
                    )
                else:
                    assert state.request.url is not None
                    download_result = await self._downloader.run(
                        job_id=state.job_id,
                        url=state.request.url,
                        job_dir=job_dir,
                        preflight=preflight,
                        logger=logger,
                        on_progress=_dl_prog,
                        cancel_event=cancel_event,
                    )
                state.artifacts.metadata_json = str((job_dir / "metadata.json").resolve())

            self._update_progress(state, JobStatus.DOWNLOADING, "downloader", 25.0)

            # 2. TRANSCRIBER (25% -> 55%)
            transcript_result: Optional[TranscriptResult] = None
            transcript_path = job_dir / "transcript.json"
            if resume and transcript_path.is_file():
                try:
                    cached_tr = TranscriptResult.model_validate_json(
                        transcript_path.read_text(encoding="utf-8")
                    )
                    if cached_tr.segments:
                        transcript_result = cached_tr
                        state.artifacts.transcript_json = str(transcript_path.resolve())
                        logger.info("transcriber", "Resume: menggunakan transcript.json yang sudah ada.")
                except Exception:
                    transcript_result = None

            if transcript_result is None:
                self._update_progress(state, JobStatus.TRANSCRIBING, "transcriber", 26.0)

                def _tr_prog(pct: float) -> None:
                    self._update_progress(state, JobStatus.TRANSCRIBING, "transcriber", 26.0 + pct * 0.29)

                transcript_result = await self._transcriber.run(
                    job_id=state.job_id,
                    download=download_result,
                    job_dir=job_dir,
                    settings=settings,
                    preflight=preflight,
                    logger=logger,
                    on_progress=_tr_prog,
                    cancel_event=cancel_event,
                )
                state.artifacts.transcript_json = str((job_dir / "transcript.json").resolve())

            self._update_progress(state, JobStatus.TRANSCRIBING, "transcriber", 55.0)

            # 3. ANALYZER (55% -> 70%)
            analysis_result: Optional[AnalysisResult] = None
            analysis_was_cached = False
            analysis_path = job_dir / "analysis.json"
            if resume and analysis_path.is_file():
                try:
                    cached_an = AnalysisResult.model_validate_json(
                        analysis_path.read_text(encoding="utf-8")
                    )
                    if cached_an.clips:
                        analysis_result = cached_an
                        analysis_was_cached = True
                        state.artifacts.analysis_json = str(analysis_path.resolve())
                        logger.info("analyzer", "Resume: menggunakan analysis.json yang sudah ada.")
                except Exception:
                    analysis_result = None

            if analysis_result is None:
                self._update_progress(state, JobStatus.ANALYZING, "analyzer", 57.0)
                an_weight = (
                    0.11
                    if getattr(settings, "enable_transcript_proofread", False)
                    and transcript_result.source != "yt_dlp_manual_sub"
                    else 0.13
                )

                def _an_prog(pct: float) -> None:
                    self._update_progress(state, JobStatus.ANALYZING, "analyzer", 57.0 + pct * an_weight)

                analysis_result = await self._analyzer.run(
                    job_id=state.job_id,
                    download=download_result,
                    transcript=transcript_result,
                    request=state.request,
                    job_dir=job_dir,
                    settings=settings,
                    logger=logger,
                    on_progress=_an_prog,
                    cancel_event=cancel_event,
                )
                state.artifacts.analysis_json = str((job_dir / "analysis.json").resolve())

            if (
                not analysis_was_cached
                and transcript_result.source != "yt_dlp_manual_sub"
                and getattr(settings, "enable_transcript_proofread", False)
                and transcript_result.segments
                and analysis_result.clips
            ):
                self._update_progress(state, JobStatus.ANALYZING, "proofreader", 68.0)

                def _pr_prog(pct: float) -> None:
                    self._update_progress(state, JobStatus.ANALYZING, "proofreader", 68.0 + pct * 0.02)

                transcript_result = await refine_transcript_result_for_clips(
                    transcript_result,
                    clips=analysis_result.clips,
                    download=download_result,
                    settings=settings,
                    logger=logger,
                    transcript_path=transcript_path,
                    cancel_event=cancel_event,
                    on_progress=_pr_prog,
                )

            self._update_progress(state, JobStatus.ANALYZING, "analyzer", 70.0)

            # 4. CUTTER (70% -> 100%)
            self._update_progress(state, JobStatus.CUTTING, "cutter", 72.0)

            def _cut_prog(pct: float) -> None:
                self._update_progress(state, JobStatus.CUTTING, "cutter", 72.0 + pct * 0.28)

            render_result: RenderResult = await self._cutter.run(
                job_id=state.job_id,
                download=download_result,
                analysis=analysis_result,
                request=state.request,
                job_dir=job_dir,
                preflight=preflight,
                logger=logger,
                on_progress=_cut_prog,
                cancel_event=cancel_event,
                transcript=transcript_result,
                settings=settings,
            )
            state.artifacts.render_result_json = str((job_dir / "render_result.json").resolve())
            self._update_progress(state, JobStatus.COMPLETED, "completed", 100.0)
            logger.info(
                "pipeline",
                f"Job selesai sukses! {len(render_result.rendered_clips)} klip tersimpan di {render_result.output_dir}",
            )
            return state

        except JobCancelledError as exc:
            logger.warning("pipeline", f"Job dibatalkan: {exc}")
            state.status = JobStatus.CANCELLED
            state.current_stage = "cancelled"
            state.error = "Dibatalkan oleh pengguna."
            self._emit_state(state)
            return state

        except Exception as exc:
            logger.error("pipeline", f"Job gagal: {exc}")
            logger.error("pipeline", traceback.format_exc())
            state.status = JobStatus.FAILED
            state.error = str(exc)
            self._emit_state(state)
            return state
