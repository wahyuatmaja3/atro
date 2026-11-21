"""Audio extraction (FFmpeg) and local transcription engine (faster-whisper)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from atro.core.logging_utils import JobLogger
from atro.core.schemas import TranscriptSegment, TranscriptWord
from atro.core.subprocess_utils import JobCancelledError, run_subprocess_streaming

_PUNCT_ONLY = set(".,!?;:…'\"”’)]}")


def build_segments_from_whisper_words(
    raw_words: list[tuple[str, float, float]],
    fallback_start: float,
    fallback_end: float,
    fallback_text: str,
    start_index: int = 0,
    pause_split_sec: float = 0.35,
) -> list[TranscriptSegment]:
    """Build pause-split TranscriptSegments with exact word onsets from Whisper word timestamps."""
    cleaned_words: list[TranscriptWord] = []
    for raw_tok, raw_s, raw_e in raw_words:
        tok = (raw_tok or "").strip()
        if not tok:
            continue
        w_start = round(max(0.0, float(raw_s)), 3)
        w_end = round(max(w_start + 0.04, float(raw_e)), 3)
        if cleaned_words and all(ch in _PUNCT_ONLY for ch in tok):
            prev = cleaned_words[-1]
            cleaned_words[-1] = TranscriptWord(
                word=prev.word + tok,
                start=prev.start,
                end=max(prev.end, w_end),
            )
        else:
            cleaned_words.append(TranscriptWord(word=tok, start=w_start, end=w_end))

    if not cleaned_words:
        text = (fallback_text or "").strip()
        start = round(max(0.0, float(fallback_start)), 3)
        end = round(float(fallback_end), 3)
        if not text or end <= start:
            return []
        return [TranscriptSegment(index=start_index, start=start, end=end, text=text)]

    groups: list[list[TranscriptWord]] = [[cleaned_words[0]]]
    for w in cleaned_words[1:]:
        prev_w = groups[-1][-1]
        gap = w.start - prev_w.end
        if gap >= pause_split_sec:
            groups.append([w])
        else:
            groups[-1].append(w)

    segments: list[TranscriptSegment] = []
    idx = start_index
    for grp in groups:
        seg_start = round(max(0.0, grp[0].start), 3)
        seg_end = round(max(seg_start + 0.08, grp[-1].end), 3)
        seg_text = " ".join(w.word for w in grp).strip()
        if seg_text and seg_end > seg_start:
            segments.append(
                TranscriptSegment(
                    index=idx,
                    start=seg_start,
                    end=seg_end,
                    text=seg_text,
                    words=grp,
                )
            )
            idx += 1
    return segments


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
        segments_iter, info = model.transcribe(
            str(audio_path),
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=300,
                speech_pad_ms=80,
            ),
            word_timestamps=True,
            condition_on_previous_text=False,
            beam_size=5,
        )

        results: list[TranscriptSegment] = []
        last_pct = -1.0
        for raw_seg in segments_iter:
            if cancel_event and cancel_event.is_set():
                raise JobCancelledError("Transkripsi Whisper dibatalkan.")

            raw_words_attr = getattr(raw_seg, "words", None) or []
            raw_word_tuples = [
                (
                    str(getattr(w, "word", "") or ""),
                    float(getattr(w, "start", raw_seg.start)),
                    float(getattr(w, "end", raw_seg.end)),
                )
                for w in raw_words_attr
            ]
            built = build_segments_from_whisper_words(
                raw_words=raw_word_tuples,
                fallback_start=float(raw_seg.start),
                fallback_end=float(raw_seg.end),
                fallback_text=str(raw_seg.text or ""),
                start_index=len(results),
            )
            results.extend(built)
            seg_end_val = built[-1].end if built else float(raw_seg.end)
            pct = min(100.0, (seg_end_val / max(video_duration_sec, 1.0)) * 100.0)
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
