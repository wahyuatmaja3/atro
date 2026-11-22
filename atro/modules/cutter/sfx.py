"""Offline transition sound effects generator & resolver (whoosh, swipe, pop)."""

from __future__ import annotations

import math
import sys
import wave
from pathlib import Path
from typing import Optional

import numpy as np

BUILTIN_SFX_NAMES: tuple[str, ...] = ("whoosh", "swipe", "pop")


def get_builtin_sfx_dir() -> Path:
    """Locate built-in `atro/assets/sfx` cleanly for both dev and PyInstaller frozen exe."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / "atro" / "assets" / "sfx"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parents[2] / "assets" / "sfx"


def _write_stereo_wav(path: Path, samples: np.ndarray, sample_rate: int = 48000) -> None:
    """Write normalized float [-1..1] mono or stereo array to 16-bit 48kHz stereo WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if samples.ndim == 1:
        stereo = np.stack([samples, samples], axis=1)
    else:
        stereo = samples
    clipped = np.clip(stereo, -0.98, 0.98)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def ensure_builtin_sfx_assets(target_dir: Optional[Path] = None) -> dict[str, Path]:
    """Generate procedural transition SFX WAVs if missing and return `{name: Path}`."""
    sfx_dir = target_dir or get_builtin_sfx_dir()
    sfx_dir.mkdir(parents=True, exist_ok=True)
    sr = 48000
    out: dict[str, Path] = {}

    # 1. Whoosh: airy resonant sweep 180Hz -> 1250Hz -> 220Hz with smooth bell envelope
    whoosh_path = sfx_dir / "whoosh.wav"
    if not whoosh_path.is_file() or whoosh_path.stat().st_size < 1024:
        rng = np.random.default_rng(42)
        n = int(sr * 0.34)
        t = np.linspace(0.0, 0.34, n, dtype=np.float64)
        env = np.sin(math.pi * (t / 0.34)) ** 1.6
        noise = rng.normal(0.0, 1.0, n)
        # Moving-window low-pass / bandpass shaping via FFT sweep weighting
        spec = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1.0 / sr)
        bp = np.exp(-0.5 * ((np.log2(np.maximum(freqs, 20.0)) - np.log2(520.0)) / 1.1) ** 2)
        bp[freqs < 80.0] = 0.0
        bp[freqs > 3600.0] *= 0.2
        shaped = np.fft.irfft(spec * bp, n=n)
        shaped /= max(1e-6, float(np.max(np.abs(shaped))))
        sweep_tone = np.sin(2.0 * math.pi * (140.0 * t + 260.0 * np.sin(math.pi * t / 0.34))) * 0.14
        sig = (shaped * 0.62 + sweep_tone) * env * 0.75
        _write_stereo_wav(whoosh_path, sig, sr)
    out["whoosh"] = whoosh_path

    # 2. Swipe: crisp directional air/chirp transient
    swipe_path = sfx_dir / "swipe.wav"
    if not swipe_path.is_file() or swipe_path.stat().st_size < 1024:
        rng = np.random.default_rng(99)
        n = int(sr * 0.24)
        t = np.linspace(0.0, 0.24, n, dtype=np.float64)
        env = (t / 0.04).clip(0.0, 1.0) * np.exp(-((t - 0.04).clip(min=0.0)) / 0.055)
        phase = 2.0 * math.pi * (950.0 * t - 1600.0 * (t ** 2))
        chirp = np.sin(phase) * 0.45
        noise = rng.normal(0.0, 1.0, n)
        spec = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1.0 / sr)
        hp = np.clip((freqs - 900.0) / 2200.0, 0.0, 1.0) * np.exp(-freqs / 5200.0)
        air = np.fft.irfft(spec * hp, n=n)
        air /= max(1e-6, float(np.max(np.abs(air))))
        sig = (chirp + air * 0.48) * env * 0.78
        _write_stereo_wav(swipe_path, sig, sr)
    out["swipe"] = swipe_path

    # 3. Pop: clean rubber-like bubble transient
    pop_path = sfx_dir / "pop.wav"
    if not pop_path.is_file() or pop_path.stat().st_size < 1024:
        n = int(sr * 0.14)
        t = np.linspace(0.0, 0.14, n, dtype=np.float64)
        env = (t / 0.008).clip(0.0, 1.0) * np.exp(-t / 0.028)
        freq = 360.0 + 620.0 * np.exp(-t / 0.025)
        phase = 2.0 * math.pi * np.cumsum(freq) / sr
        sig = np.sin(phase) * env * 0.80
        _write_stereo_wav(pop_path, sig, sr)
    out["pop"] = pop_path

    return out


def resolve_transition_sfx_path(sfx_name: str) -> Optional[Path]:
    """Return local WAV path for a built-in transition SFX name or `None` if `none`."""
    clean = (sfx_name or "none").strip().lower()
    if clean not in BUILTIN_SFX_NAMES:
        return None
    sfx_dir = get_builtin_sfx_dir()
    candidate = sfx_dir / f"{clean}.wav"
    if candidate.is_file() and candidate.stat().st_size >= 1024:
        return candidate
    assets = ensure_builtin_sfx_assets(sfx_dir)
    return assets.get(clean)
