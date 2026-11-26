"""TranscriberService orchestrating subtitle validation and faster-whisper fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.schemas import (
    DownloadResult,
    PreflightReport,
    SubtitleCandidate,
    TranscriptResult,
    TranscriptSource,
)
from atro.modules.transcriber.proofreader import (
    build_whisper_hotwords,
    build_whisper_initial_prompt,
    clean_transcript_segments_offline,
    infer_language_hint,
)
from atro.modules.transcriber.subtitle_parser import evaluate_subtitle_quality, parse_subtitle_file
from atro.modules.transcriber.whisper_engine import extract_audio_wav, transcribe_with_faster_whisper


def format_transcript_lines(transcript: TranscriptResult) -> str:
    """Format transcript segments into precise `[start - end] text` lines for LLM analysis."""
    return "\n".join(
        f"[{seg.start:.2f} - {seg.end:.2f}] {seg.text}"
        for seg in transcript.segments
    )


def _sort_candidates(candidates: list[SubtitleCandidate], preferred_lang: Optional[str]) -> list[SubtitleCandidate]:
    def _rank(cand: SubtitleCandidate) -> tuple[int, int, int]:
        manual_rank = 0 if not cand.is_auto_generated else 1
        lang_low = cand.lang.lower()
        pref_low = (preferred_lang or "").lower()
        if pref_low and (lang_low == pref_low or lang_low.startswith(f"{pref_low}-")):
            lang_rank = 0
        elif lang_low == "id" or lang_low.startswith("id-"):
            lang_rank = 1
        elif lang_low == "en" or lang_low.startswith("en-"):
            lang_rank = 2
        else:
            lang_rank = 3
        ext_rank = 0 if cand.ext.lower() == "vtt" else (1 if cand.ext.lower() == "srt" else 2)
        return (manual_rank, lang_rank, ext_rank)

    return sorted(candidates, key=_rank)


class TranscriberService:
    """Produces timestamped TranscriptResult from subtitles or local faster-whisper."""

    async def run(
        self,
        job_id: str,
        download: DownloadResult,
        job_dir: Path,
        settings: AppSettings,
        preflight: PreflightReport,
        logger: JobLogger,
        on_progress: Optional[Callable[[float], Awaitable[None] | None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> TranscriptResult:
        if cancel_event and cancel_event.is_set():
            from atro.core.subprocess_utils import JobCancelledError
            raise JobCancelledError("Job dibatalkan sebelum transkripsi.")

        # Stage 1: Try candidate subtitles downloaded by yt-dlp
        ordered_candidates = _sort_candidates(download.subtitle_candidates, download.language)
        for cand in ordered_candidates:
            sub_path = Path(cand.local_path)
            if not sub_path.is_file():
                continue
            if cand.ext.lower() not in {"vtt", "srt"}:
                logger.info("transcriber", f"Lewati subtitle format .{cand.ext}: {sub_path.name}")
                continue

            try:
                raw_text = sub_path.read_text(encoding="utf-8", errors="replace")
                segments = parse_subtitle_file(raw_text, cand.ext)
                is_ok, notes = evaluate_subtitle_quality(
                    segments,
                    video_duration_sec=download.duration_sec,
                    is_auto_generated=cand.is_auto_generated,
                )
                if is_ok:
                    source: TranscriptSource = (
                        "yt_dlp_auto_sub" if cand.is_auto_generated else "yt_dlp_manual_sub"
                    )
                    logger.info("transcriber", f"Memakai subtitle bawaan ({sub_path.name}): {notes}")
                    segments = clean_transcript_segments_offline(
                        segments,
                        title=download.title,
                        channel=download.channel,
                    )
                    if on_progress:
                        res = on_progress(100.0)
                        if asyncio.iscoroutine(res):
                            await res
                    result = TranscriptResult(
                        job_id=job_id,
                        source=source,
                        language=cand.lang,
                        model_used=None,
                        quality_notes=notes,
                        segments=segments,
                    )
                    (job_dir / "transcript.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
                    return result
                logger.warning("transcriber", f"Subtitle {sub_path.name} ditolak: {notes}")
            except Exception as exc:
                logger.warning("transcriber", f"Gagal mem-parse subtitle {sub_path.name}: {exc}")

        # Stage 2: Fallback to local faster-whisper
        if not download.has_audio:
            raise RuntimeError("Video tidak memiliki stream audio dan tidak ada subtitle yang valid.")
        if not preflight.ffmpeg.available or not preflight.ffmpeg.path:
            raise RuntimeError("Binary ffmpeg tidak tersedia untuk ekstraksi audio.")

        logger.info("transcriber", "Tidak ada subtitle akurat; beralih ke faster-whisper lokal...")
        if on_progress:
            res = on_progress(5.0)
            if asyncio.iscoroutine(res):
                await res

        audio_path = job_dir / "audio.wav"
        await extract_audio_wav(
            source_video=Path(download.source_video_path),
            audio_out=audio_path,
            ffmpeg_path=Path(preflight.ffmpeg.path),
            logger=logger,
            cancel_event=cancel_event,
        )

        if on_progress:
            res = on_progress(15.0)
            if asyncio.iscoroutine(res):
                await res

        async def _whisper_progress(pct: float) -> None:
            if on_progress:
                scaled = 15.0 + (pct * 0.85)
                res = on_progress(min(100.0, scaled))
                if asyncio.iscoroutine(res):
                    await res

        lang_hint = infer_language_hint(
            download.language,
            title=download.title,
            channel=download.channel,
            explicit_pref=getattr(settings, "whisper_language", "auto"),
        )
        initial_prompt = build_whisper_initial_prompt(
            title=download.title,
            channel=download.channel,
            language=lang_hint,
        )
        hotwords = build_whisper_hotwords(title=download.title, channel=download.channel)

        segments, detected_lang, model_used = await transcribe_with_faster_whisper(
            audio_path=audio_path,
            video_duration_sec=download.duration_sec,
            model_name=settings.whisper_model,
            device_pref=settings.whisper_device,
            logger=logger,
            on_progress=_whisper_progress,
            cancel_event=cancel_event,
            language_hint=lang_hint,
            initial_prompt=initial_prompt,
            hotwords=hotwords,
        )
        if not segments:
            raise RuntimeError("faster-whisper tidak menghasilkan segmen transkrip apa pun.")

        segments = clean_transcript_segments_offline(
            segments,
            title=download.title,
            channel=download.channel,
        )

        result = TranscriptResult(
            job_id=job_id,
            source="faster_whisper",
            language=detected_lang or download.language,
            model_used=model_used,
            quality_notes=f"Dihasilkan oleh faster-whisper ({model_used}), {len(segments)} segmen.",
            segments=segments,
        )
        (job_dir / "transcript.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        if on_progress:
            res = on_progress(100.0)
            if asyncio.iscoroutine(res):
                await res
        logger.info("transcriber", f"Transkripsi Whisper selesai: {len(segments)} segmen (bahasa={result.language}).")
        return result
