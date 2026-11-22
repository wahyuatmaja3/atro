"""Offline CPU speaker diarization (sherpa-onnx pyannote + CAM++) with per-job caching."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field


class SpeakerSegment(BaseModel):
    """Single diarized speech segment with absolute source video timestamps."""

    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    speaker_id: str
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)


class SpeakerFaceBinding(BaseModel):
    """Learned correspondence between an audio speaker ID and visual face cluster ID."""

    speaker_id: str
    face_id: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    avg_cx: float = Field(default=0.5, ge=0.0, le=1.0)
    sample_count: int = Field(default=0, ge=0)


class DiarizationWindowCache(BaseModel):
    """Cached diarization output for a specific `[start_sec, end_sec]` window."""

    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(gt=0.0)
    method: str = "sherpa_onnx"
    segments: list[SpeakerSegment] = Field(default_factory=list)


class JobSpeakerMap(BaseModel):
    """Persistent `<job_dir>/speaker_map.json` cache across clip edits and batch renders."""

    version: int = 1
    windows: list[DiarizationWindowCache] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    bindings: list[SpeakerFaceBinding] = Field(default_factory=list)


def _resolve_diarization_models() -> Optional[tuple[Path, Path]]:
    """Locate bundled pyannote int8 segmentation and 3D-Speaker CAM++ ONNX models."""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass) / "atro" / "assets" / "diarization")
    roots.append(Path(__file__).resolve().parents[3] / "assets" / "diarization")

    for base in roots:
        if not base.is_dir():
            continue
        seg_int8 = base / "sherpa-onnx-pyannote-segmentation-3-0" / "model.int8.onnx"
        seg_fp32 = base / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
        emb = base / "speaker_embed.onnx"
        seg = seg_int8 if (seg_int8.is_file() and seg_int8.stat().st_size > 100_000) else seg_fp32
        if seg.is_file() and emb.is_file() and emb.stat().st_size > 100_000:
            return seg, emb
    return None


def _resample_linear_16k(samples: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == 16000 or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    dst_len = max(1, int(round(samples.size * (16000.0 / float(src_rate)))))
    x_old = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, dst_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def _read_wav_slice_16k(wav_path: Path, start_sec: float, end_sec: Optional[float]) -> Optional[np.ndarray]:
    try:
        with wave.open(str(wav_path), "rb") as wf:
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            total_frames = wf.getnframes()
            if sr <= 0 or total_frames <= 0 or sw not in (2, 4):
                return None
            f0 = max(0, min(total_frames, int(round(start_sec * sr))))
            f1 = total_frames if end_sec is None else max(f0, min(total_frames, int(round(end_sec * sr))))
            if f1 <= f0:
                return np.zeros(0, dtype=np.float32)
            wf.setpos(f0)
            raw = wf.readframes(f1 - f0)
    except Exception:
        return None

    if not raw:
        return np.zeros(0, dtype=np.float32)

    if sw == 2:
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        arr = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0

    if n_ch > 1:
        trim_len = (arr.size // n_ch) * n_ch
        arr = arr[:trim_len].reshape(-1, n_ch).mean(axis=1)

    return _resample_linear_16k(arr, sr)


def load_pcm_16k_mono(
    source_path: Path,
    start_sec: float = 0.0,
    end_sec: Optional[float] = None,
    ffmpeg_path: Optional[Path] = None,
) -> np.ndarray:
    """Load mono 16kHz float32 PCM for `[start_sec, end_sec]` from WAV or video."""
    st = max(0.0, float(start_sec))
    if source_path.suffix.lower() == ".wav" and source_path.is_file():
        sliced = _read_wav_slice_16k(source_path, st, end_sec)
        if sliced is not None:
            return sliced

    # Reuse job audio.wav if sitting right beside source_video_path
    sibling_wav = source_path.with_name("audio.wav")
    if sibling_wav.is_file():
        sliced = _read_wav_slice_16k(sibling_wav, st, end_sec)
        if sliced is not None:
            return sliced

    ff = ffmpeg_path
    if ff is None:
        which_ff = shutil.which("ffmpeg")
        if which_ff:
            ff = Path(which_ff)
    if ff is None or not ff.is_file() or not source_path.is_file():
        return np.zeros(0, dtype=np.float32)

    cmd = [
        str(ff),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if st > 0.0:
        cmd.extend(["-ss", f"{st:.3f}"])
    if end_sec is not None and end_sec > st:
        cmd.extend(["-t", f"{end_sec - st:.3f}"])
    cmd.extend(["-i", str(source_path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"])

    try:
        proc = subprocess.run(cmd, capture_output=True, check=False, timeout=45)
        if proc.returncode != 0 or not proc.stdout:
            return np.zeros(0, dtype=np.float32)
        arr = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        return arr
    except Exception:
        return np.zeros(0, dtype=np.float32)


def _normalize_speaker_segments(
    raw_items: list[tuple[float, float, str, float]],
    offset_sec: float = 0.0,
) -> list[SpeakerSegment]:
    """Merge short pauses per speaker and rank speaker IDs by total speaking duration."""
    if not raw_items:
        return []

    filtered = [
        (max(0.0, float(s)), max(0.0, float(e)), str(lbl), float(conf))
        for s, e, lbl, conf in raw_items
        if (float(e) - float(s)) >= 0.22
    ]
    if not filtered:
        return []

    filtered.sort(key=lambda item: (item[0], item[1]))
    totals: dict[str, float] = {}
    for s, e, lbl, _ in filtered:
        totals[lbl] = totals.get(lbl, 0.0) + (e - s)

    ranked_raw = sorted(totals.keys(), key=lambda k: totals[k], reverse=True)
    remap = {old: f"SPK_{idx + 1}" for idx, old in enumerate(ranked_raw)}

    merged: list[SpeakerSegment] = []
    for s, e, raw_lbl, conf in filtered:
        spk = remap[raw_lbl]
        abs_s = round(offset_sec + s, 3)
        abs_e = round(offset_sec + e, 3)
        if abs_e <= abs_s:
            continue
        if merged and merged[-1].speaker_id == spk and abs_s - merged[-1].end <= 0.35:
            merged[-1].end = max(merged[-1].end, abs_e)
            merged[-1].confidence = max(merged[-1].confidence, round(conf, 3))
        else:
            merged.append(
                SpeakerSegment(
                    start=abs_s,
                    end=abs_e,
                    speaker_id=spk,
                    confidence=round(max(0.0, min(1.0, conf)), 3),
                )
            )
    return merged


def _run_sherpa_diarization_sync(
    samples: np.ndarray,
    offset_sec: float = 0.0,
    expected_speakers: int = -1,
    clustering_threshold: float = 0.58,
) -> Optional[list[SpeakerSegment]]:
    """Run local CPU sherpa-onnx diarization if package and ONNX models exist."""
    if samples.size < 4000:
        return []
    models = _resolve_diarization_models()
    if models is None:
        return None
    seg_path, emb_path = models
    try:
        import sherpa_onnx
    except Exception:
        return None

    try:
        threads = max(1, min(4, (os.cpu_count() or 2)))
        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(seg_path)
                )
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(emb_path),
                num_threads=threads,
                provider="cpu",
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=int(expected_speakers) if expected_speakers > 0 else -1,
                threshold=float(clustering_threshold),
            ),
            min_duration_on=0.30,
            min_duration_off=0.30,
        )
        sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
        result = sd.process(samples)
        raw_segs = result.sort_by_start_time()
        raw_items = [
            (float(s.start), float(s.end), f"S{int(s.speaker)}", 0.90)
            for s in raw_segs
        ]
        return _normalize_speaker_segments(raw_items, offset_sec=offset_sec)
    except Exception:
        return None


def _fallback_spectral_diarization_sync(
    samples: np.ndarray,
    offset_sec: float = 0.0,
    sample_rate: int = 16000,
) -> list[SpeakerSegment]:
    """Lightweight pure-numpy RMS + spectral-centroid VAD diarizer fallback."""
    if samples.size < sample_rate // 2:
        return []

    win = int(round(sample_rate * 0.20))
    hop = int(round(sample_rate * 0.10))
    if samples.size <= win:
        return []

    frames: list[tuple[float, float, float, float]] = []
    pos = 0
    freqs = np.fft.rfftfreq(win, d=1.0 / sample_rate)
    while pos + win <= samples.size:
        chunk = samples[pos : pos + win]
        rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-12))
        mag = np.abs(np.fft.rfft(chunk * np.hanning(win)))
        mag_sum = float(np.sum(mag) + 1e-9)
        centroid = float(np.sum(freqs * mag) / mag_sum)
        t0 = pos / float(sample_rate)
        t1 = (pos + win) / float(sample_rate)
        frames.append((t0, t1, rms, centroid))
        pos += hop

    if not frames:
        return []

    rms_vals = np.array([f[2] for f in frames], dtype=np.float32)
    noise_floor = float(np.percentile(rms_vals, 20))
    speech_thresh = max(0.012, min(0.06, noise_floor * 2.5 + 0.008))

    speech_centroids = [f[3] for f in frames if f[2] >= speech_thresh]
    if not speech_centroids:
        return []
    median_c = float(np.median(speech_centroids))

    raw_items: list[tuple[float, float, str, float]] = []
    cur_spk: Optional[str] = None
    seg_s = 0.0
    seg_e = 0.0

    for t0, t1, rms, centroid in frames:
        if rms < speech_thresh:
            if cur_spk is not None and (t0 - seg_e) > 0.35:
                raw_items.append((seg_s, seg_e, cur_spk, 0.55))
                cur_spk = None
            continue
        spk = "C0" if centroid <= median_c else "C1"
        if cur_spk is None:
            cur_spk = spk
            seg_s = t0
            seg_e = t1
        elif spk == cur_spk and (t0 - seg_e) <= 0.45:
            seg_e = t1
        else:
            raw_items.append((seg_s, seg_e, cur_spk, 0.55))
            cur_spk = spk
            seg_s = t0
            seg_e = t1

    if cur_spk is not None:
        raw_items.append((seg_s, seg_e, cur_spk, 0.55))

    return _normalize_speaker_segments(raw_items, offset_sec=offset_sec)


def load_job_speaker_map(job_dir: Optional[Path]) -> JobSpeakerMap:
    """Load cached `<job_dir>/speaker_map.json` safely."""
    if job_dir is None:
        return JobSpeakerMap()
    cache_path = job_dir / "speaker_map.json"
    if not cache_path.is_file():
        return JobSpeakerMap()
    try:
        return JobSpeakerMap.model_validate_json(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return JobSpeakerMap()


def save_job_speaker_map(job_dir: Path, data: JobSpeakerMap) -> Path:
    """Atomically write `<job_dir>/speaker_map.json`."""
    job_dir.mkdir(parents=True, exist_ok=True)
    target = job_dir / "speaker_map.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(data.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def diarize_audio_window_sync(
    source_path: Path,
    start_sec: float = 0.0,
    end_sec: Optional[float] = None,
    job_dir: Optional[Path] = None,
    ffmpeg_path: Optional[Path] = None,
    expected_speakers: int = -1,
) -> list[SpeakerSegment]:
    """Run (or reuse cached) offline speaker diarization over `[start_sec, end_sec]`."""
    st = max(0.0, float(start_sec))
    en = float(end_sec) if end_sec is not None else None

    if job_dir is not None and en is not None:
        smap = load_job_speaker_map(job_dir)
        for win in smap.windows:
            if win.start_sec <= st + 0.6 and win.end_sec >= en - 0.6:
                return [
                    SpeakerSegment(
                        start=max(st, seg.start),
                        end=min(en, seg.end),
                        speaker_id=seg.speaker_id,
                        confidence=seg.confidence,
                    )
                    for seg in win.segments
                    if seg.end > st and seg.start < en and min(en, seg.end) - max(st, seg.start) >= 0.15
                ]

    samples = load_pcm_16k_mono(source_path, start_sec=st, end_sec=en, ffmpeg_path=ffmpeg_path)
    if samples.size < 4000:
        return []

    method = "sherpa_onnx"
    segs = _run_sherpa_diarization_sync(
        samples,
        offset_sec=st,
        expected_speakers=expected_speakers,
    )
    if segs is None:
        method = "spectral_fallback"
        segs = _fallback_spectral_diarization_sync(samples, offset_sec=st)

    actual_end = en if en is not None else round(st + samples.size / 16000.0, 3)
    if job_dir is not None and actual_end > st:
        smap = load_job_speaker_map(job_dir)
        smap.windows = [
            w
            for w in smap.windows
            if not (abs(w.start_sec - st) < 1.0 and abs(w.end_sec - actual_end) < 1.0)
        ]
        smap.windows.append(
            DiarizationWindowCache(
                start_sec=round(st, 3),
                end_sec=round(actual_end, 3),
                method=method,
                segments=segs,
            )
        )
        save_job_speaker_map(job_dir, smap)

    return segs


def active_speakers_in_range(
    segments: list[SpeakerSegment],
    t0: float,
    t1: float,
) -> dict[str, float]:
    """Return total active speaking seconds per speaker inside `[t0, t1]`."""
    out: dict[str, float] = {}
    for seg in segments:
        ov_s = max(t0, seg.start)
        ov_e = min(t1, seg.end)
        if ov_e > ov_s:
            out[seg.speaker_id] = out.get(seg.speaker_id, 0.0) + (ov_e - ov_s)
    return out
