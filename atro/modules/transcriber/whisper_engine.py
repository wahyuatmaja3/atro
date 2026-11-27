"""Audio extraction (FFmpeg) and local transcription engine (faster-whisper)."""

from __future__ import annotations

import asyncio
import ctypes
import gc
import inspect
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional

from atro.core.logging_utils import JobLogger
from atro.core.schemas import TranscriptSegment, TranscriptWord
from atro.core.subprocess_utils import JobCancelledError, run_subprocess_streaming

_PUNCT_ONLY = set(".,!?;:…'\"”’)]}")

_SPEED_PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "use_batched": True,
        "cpu_batch_size": 8,
        "cuda_batch_size": 16,
        "beam_size": 1,
        "best_of": 1,
        "temperature": (0.0, 0.2),
        "use_hotwords": False,
    },
    "balanced": {
        "use_batched": True,
        "cpu_batch_size": 8,
        "cuda_batch_size": 16,
        "beam_size": 2,
        "best_of": 2,
        "temperature": (0.0, 0.2, 0.4),
        "use_hotwords": True,
    },
    "accurate": {
        "use_batched": False,
        "cpu_batch_size": 1,
        "cuda_batch_size": 1,
        "beam_size": 5,
        "best_of": 5,
        "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        "use_hotwords": True,
    },
}

_MODEL_LOCK = threading.Lock()
_CACHED_ENTRY: Optional[tuple[tuple[Any, ...], Any, Any, Any]] = None
_ADDED_DLL_DIRS: set[str] = set()
_ADDED_DLL_HANDLES: list[Any] = []


def clear_whisper_model_cache() -> None:
    """Release any cached in-process WhisperModel instance."""
    global _CACHED_ENTRY
    with _MODEL_LOCK:
        _CACHED_ENTRY = None
    gc.collect()


def get_optimal_cpu_threads() -> int:
    """Return physical CPU core count (clamped 1..8) to avoid SMT contention in CTranslate2."""
    physical: Optional[int] = None
    if sys.platform == "win32":
        try:
            import ctypes.wintypes

            class _SysLogicalProcInfo(ctypes.Structure):
                _fields_ = [
                    ("ProcessorMask", ctypes.c_size_t),
                    ("Relationship", ctypes.c_int),
                    ("Flags", ctypes.c_byte * 16),
                ]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            buf_len = ctypes.wintypes.DWORD(0)
            k32.GetLogicalProcessorInformation(None, ctypes.byref(buf_len))
            item_sz = ctypes.sizeof(_SysLogicalProcInfo)
            if buf_len.value >= item_sz > 0:
                arr = (_SysLogicalProcInfo * (buf_len.value // item_sz))()
                if k32.GetLogicalProcessorInformation(arr, ctypes.byref(buf_len)):
                    cnt = sum(1 for item in arr if item.Relationship == 0)
                    if cnt >= 1:
                        physical = cnt
        except Exception:
            physical = None
    elif Path("/proc/cpuinfo").is_file():
        try:
            pairs: set[tuple[str, str]] = set()
            phys_id = "0"
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("physical id"):
                    phys_id = line.split(":", 1)[-1].strip()
                elif line.startswith("core id"):
                    pairs.add((phys_id, line.split(":", 1)[-1].strip()))
            if pairs:
                physical = len(pairs)
        except Exception:
            physical = None

    if physical is None:
        logical = os.cpu_count() or 4
        physical = max(2, logical // 2) if logical >= 4 else max(1, logical)
    return max(1, min(8, int(physical)))


def _register_nvidia_dll_dirs() -> None:
    """Register bundled site-packages NVIDIA DLL folders on Windows if installed."""
    if sys.platform != "win32" or not hasattr(os, "add_dll_directory"):
        return
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not base.is_dir():
        return
    for sub in ("cublas", "cudnn", "cuda_runtime"):
        bin_dir = base / sub / "bin"
        if bin_dir.is_dir():
            s = str(bin_dir)
            if s not in _ADDED_DLL_DIRS:
                try:
                    _ADDED_DLL_HANDLES.append(os.add_dll_directory(s))
                    _ADDED_DLL_DIRS.add(s)
                except Exception:
                    pass


def _probe_cuda_ready() -> tuple[bool, str]:
    """Fast pre-check whether CTranslate2 CUDA + cublas64_12 runtime are usable before loading weights."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return False, "tidak ada GPU CUDA"
    except Exception as exc:
        return False, f"ctranslate2 CUDA check gagal: {exc}"

    if sys.platform == "win32":
        _register_nvidia_dll_dirs()
        try:
            ctypes.WinDLL("cublas64_12.dll")
        except OSError:
            return False, "cublas64_12.dll belum terpasang"
    return True, "CUDA siap"


def _select_cuda_compute_type() -> str:
    """Pick optimal supported CUDA compute type (int8_float16 on Tensor Core GPUs, else int8)."""
    try:
        import ctranslate2

        supported = set(ctranslate2.get_supported_compute_types("cuda"))
        if "int8_float16" in supported and "float16" in supported:
            return "int8_float16"
        if "int8" in supported:
            return "int8"
        if "float16" in supported:
            return "float16"
    except Exception:
        pass
    return "int8"


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
    skip_if_fresh: bool = True,
) -> Path:
    """Extract mono 16kHz PCM WAV from source video for faster-whisper."""
    if (
        skip_if_fresh
        and audio_out.is_file()
        and audio_out.stat().st_size > 1024
        and source_video.is_file()
        and audio_out.stat().st_mtime >= source_video.stat().st_mtime
    ):
        logger.info("transcriber", f"Menggunakan audio 16kHz cached ({audio_out.name}) untuk percepat proses.")
        return audio_out

    audio_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(ffmpeg_path),
        "-y",
        "-threads",
        "0",
        "-i",
        str(source_video),
        "-vn",
        "-sn",
        "-dn",
        "-af",
        "highpass=f=65,lowpass=f=7600",
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


def _get_or_load_whisper_model(
    model_cls: Any,
    batched_cls: Any,
    model_name: str,
    device: str,
    compute_type: str,
    cpu_threads: int,
    use_batched: bool,
) -> tuple[Any, Any, bool]:
    """Return `(model, batched_pipeline_or_none, cache_hit)` reusing the active loaded model."""
    global _CACHED_ENTRY
    key = (model_name, device, compute_type, cpu_threads if device == "cpu" else 0)
    with _MODEL_LOCK:
        if _CACHED_ENTRY is not None and _CACHED_ENTRY[0] == key and _CACHED_ENTRY[1] is model_cls:
            cached_model = _CACHED_ENTRY[2]
            cached_batched = _CACHED_ENTRY[3]
            if use_batched and batched_cls is not None and cached_batched is None:
                try:
                    cached_batched = batched_cls(cached_model)
                    _CACHED_ENTRY = (key, model_cls, cached_model, cached_batched)
                except Exception:
                    cached_batched = None
            return cached_model, cached_batched, True
        if _CACHED_ENTRY is not None:
            _CACHED_ENTRY = None
            gc.collect()

    init_kw: dict[str, Any] = {"device": device, "compute_type": compute_type}
    try:
        init_sig = set(inspect.signature(model_cls.__init__).parameters.keys())
        if device == "cpu" and "cpu_threads" in init_sig:
            init_kw["cpu_threads"] = cpu_threads
    except Exception:
        pass
    model = model_cls(model_name, **init_kw)
    batched = None
    if use_batched and batched_cls is not None:
        try:
            batched = batched_cls(model)
        except Exception:
            batched = None

    with _MODEL_LOCK:
        _CACHED_ENTRY = (key, model_cls, model, batched)
    return model, batched, False


def _run_faster_whisper_sync(
    audio_path: Path,
    video_duration_sec: float,
    model_name: str,
    device_pref: str,
    logger: JobLogger,
    loop: asyncio.AbstractEventLoop,
    on_progress: Optional[Callable[[float], Awaitable[None] | None]],
    cancel_event: Optional[asyncio.Event],
    language_hint: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    hotwords: Optional[str] = None,
    speed_mode: str = "fast",
) -> tuple[list[TranscriptSegment], Optional[str], str]:
    import faster_whisper

    model_cls = faster_whisper.WhisperModel
    batched_cls = getattr(faster_whisper, "BatchedInferencePipeline", None)
    eff_mode = speed_mode if speed_mode in _SPEED_PROFILES else "fast"
    profile = _SPEED_PROFILES[eff_mode]
    cpu_threads = get_optimal_cpu_threads()

    def _emit_progress(pct: float) -> None:
        if on_progress is None:
            return
        res = on_progress(pct)
        if asyncio.iscoroutine(res):
            asyncio.run_coroutine_threadsafe(res, loop)

    def _transcribe_with_device(device: str, compute_type: str) -> tuple[list[TranscriptSegment], Optional[str]]:
        t_start = time.perf_counter()
        use_batched = bool(profile["use_batched"] and batched_cls is not None)
        model, batched_pipe, cache_hit = _get_or_load_whisper_model(
            model_cls=model_cls,
            batched_cls=batched_cls,
            model_name=model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            use_batched=use_batched,
        )
        pipe = batched_pipe if (use_batched and batched_pipe is not None) else model
        is_batched_active = pipe is not model
        batch_sz = int(profile["cuda_batch_size"] if device == "cuda" else profile["cpu_batch_size"])
        thr_note = f", threads={cpu_threads}" if device == "cpu" else ""
        bat_note = f", batched={batch_sz}" if is_batched_active else ", sequential"
        cache_note = " (cached)" if cache_hit else ""
        logger.info(
            "transcriber",
            f"faster-whisper model='{model_name}'{cache_note}, mode='{eff_mode}'{bat_note}, "
            f"device='{device}', compute='{compute_type}'{thr_note}, lang='{language_hint or 'auto'}'...",
        )

        supported_params = set(inspect.signature(pipe.transcribe).parameters.keys())
        tx_kwargs: dict[str, Any] = {
            "vad_filter": True,
            "vad_parameters": {
                "min_silence_duration_ms": 260,
                "speech_pad_ms": 160,
            },
            "word_timestamps": True,
            "condition_on_previous_text": False,
            "beam_size": int(profile["beam_size"]),
        }
        if "best_of" in supported_params and not is_batched_active:
            tx_kwargs["best_of"] = int(profile["best_of"])
        if "temperature" in supported_params:
            tx_kwargs["temperature"] = list(profile["temperature"])
        if is_batched_active and "batch_size" in supported_params:
            tx_kwargs["batch_size"] = batch_sz
        if language_hint and "language" in supported_params:
            tx_kwargs["language"] = language_hint
        if initial_prompt and "initial_prompt" in supported_params:
            tx_kwargs["initial_prompt"] = initial_prompt
        if profile["use_hotwords"] and hotwords and "hotwords" in supported_params:
            tx_kwargs["hotwords"] = hotwords
        if "repetition_penalty" in supported_params:
            tx_kwargs["repetition_penalty"] = 1.08
        if "no_repeat_ngram_size" in supported_params:
            tx_kwargs["no_repeat_ngram_size"] = 3
        if "hallucination_silence_threshold" in supported_params:
            tx_kwargs["hallucination_silence_threshold"] = 1.5

        segments_iter, info = pipe.transcribe(str(audio_path), **tx_kwargs)

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

        if last_pct < 100.0:
            _emit_progress(100.0)

        elapsed = time.perf_counter() - t_start
        logger.info("transcriber", f"Proses Whisper selesai dalam {elapsed:.2f} detik ({len(results)} segmen).")
        detected_lang = getattr(info, "language", None)
        return results, detected_lang

    if device_pref in ("auto", "cuda"):
        cuda_ok, cuda_reason = _probe_cuda_ready()
        if cuda_ok:
            cuda_ctype = _select_cuda_compute_type()
            try:
                segs, lang = _transcribe_with_device("cuda", cuda_ctype)
                return segs, lang, f"{model_name} (cuda/{cuda_ctype})"
            except JobCancelledError:
                raise
            except Exception as exc:
                if device_pref == "cuda":
                    logger.warning("transcriber", f"CUDA Whisper gagal ({exc}); fallback ke CPU int8...")
                else:
                    logger.warning("transcriber", f"GPU CUDA tidak siap ({exc}); fallback otomatis ke CPU int8.")
        else:
            if device_pref == "cuda":
                logger.warning("transcriber", f"CUDA dipilih tetapi tidak siap ({cuda_reason}); fallback ke CPU int8.")
            else:
                logger.info("transcriber", f"GPU CUDA dilewati ({cuda_reason}); memakai CPU int8 (threads={cpu_threads}).")

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
    language_hint: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    hotwords: Optional[str] = None,
    speed_mode: str = "fast",
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
        language_hint,
        initial_prompt,
        hotwords,
        speed_mode,
    )
