"""Audio extraction (FFmpeg) and local transcription engine (faster-whisper)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from atro.core.logging_utils import JobLogger
from atro.core.schemas import TranscriptSegment
from atro.core.subprocess_utils import JobCancelledError, run_subprocess_streaming


async def extract_audio_wav(
    source_video: Path,
    audio_out: Path,
    ffmpeg_path: Path,
    logger: JobLogger,
    cancel_event: Optional[asyncio.Event] = None,
) -> Path:
    """Extract mono 16kHz PCM WAV from source video for faster-whisper."""
    audio_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(source_video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio_out),
    ]
    logger.info("transcriber", f"Mengekstrak audio 16kHz mono ke {audio_out.name}...")
    rc, lines = await run_subprocess_streaming(cmd, cancel_event=cancel_event, cwd=audio_out.parent)
    if rc != 0 or not audio_out.is_file():
        raise RuntimeError(f"Gagal mengekstrak audio WAV (exit {rc}): {' | '.join(lines[-5:])}")
    return audio_out


def _run_faster_whisper_sync(
    audio_path: Path,
    video_duration_sec: float,
    model_name: str,
    device_pref: str,
    logger: JobLogger,
    loop: asyncio.AbstractEventLoop,
    on_progress: Optional[Callable[[float], Awaitable[None] | None]],
    cancel_event: Optional[asyncio.Event],
) -> tuple[list[TranscriptSegment], Optional[str], str]:
    from faster_whisper import WhisperModel

    def _emit_progress(pct: float) -> None:
        if on_progress is None:
            return
        res = on_progress(pct)
        if asyncio.iscoroutine(res):
            asyncio.run_coroutine_threadsafe(res, loop)

    def _transcribe_with_device(device: str, compute_type: str) -> tuple[list[TranscriptSegment], Optional[str]]:
        logger.info(
            "transcriber",
            f"Memuat faster-whisper model='{model_name}', device='{device}', compute_type='{compute_type}'...",
        )
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments_iter, info = model.transcribe(str(audio_path), vad_filter=True, beam_size=5)

        results: list[TranscriptSegment] = []
        last_pct = -1.0
        for raw_seg in segments_iter:
            if cancel_event and cancel_event.is_set():
                raise JobCancelledError("Transkripsi Whisper dibatalkan.")

            text = (raw_seg.text or "").strip()
            start = round(max(0.0, float(raw_seg.start)), 3)
            end = round(float(raw_seg.end), 3)
            if not text or end <= start:
                continue

            results.append(
                TranscriptSegment(
                    index=len(results),
                    start=start,
                    end=end,
                    text=text,
                )
            )
            pct = min(100.0, (end / max(video_duration_sec, 1.0)) * 100.0)
            if pct - last_pct >= 2.0 or pct >= 100.0:
                last_pct = pct
                _emit_progress(pct)

        detected_lang = getattr(info, "language", None)
        return results, detected_lang

    if device_pref in ("auto", "cuda"):
        try:
            segs, lang = _transcribe_with_device("cuda", "int8")
            return segs, lang, f"{model_name} (cuda/int8)"
        except JobCancelledError:
            raise
        except Exception as exc:
            if device_pref == "cuda":
                logger.warning("transcriber", f"CUDA Whisper gagal ({exc}); fallback ke CPU int8...")
            else:
                logger.warning("transcriber", f"GPU CUDA/cuDNN tidak siap ({exc}); fallback otomatis ke CPU int8.")

    segs, lang = _transcribe_with_device("cpu", "int8")
    return segs, lang, f"{model_name} (cpu/int8)"


async def transcribe_with_faster_whisper(
    audio_path: Path,
    video_duration_sec: float,
    model_name: str,
    device_pref: str,
    logger: JobLogger,
    on_progress: Optional[Callable[[float], Awaitable[None] | None]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> tuple[list[TranscriptSegment], Optional[str], str]:
    """Run local faster-whisper transcription off the event loop with safe CUDA->CPU fallback."""
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(
        _run_faster_whisper_sync,
        audio_path,
        video_duration_sec,
        model_name,
        device_pref,
        logger,
        loop,
        on_progress,
        cancel_event,
    )
