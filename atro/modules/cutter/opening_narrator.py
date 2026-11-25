"""Opening title voiceover synthesis using edge-tts with Windows SAPI5 offline fallback."""

from __future__ import annotations

import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Optional

from atro.core.logging_utils import JobLogger
from atro.core.subprocess_utils import run_subprocess_capture

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\w+")
_ALLOWED_CHARS_RE = re.compile(r"[^\w\s.,!?;:'\"()\-%/]+", re.UNICODE)
_MULTI_PUNCT_RE = re.compile(r"([!?.,])\1+")
_WHITESPACE_RE = re.compile(r"\s+")
_PCT_RE = re.compile(r"([+-]?\d+)\s*%")

TTS_VOICE_CHOICES: tuple[tuple[str, str], ...] = (
    ("Indonesia Cewek - Cerah & Enerjik (Gadis)", "id-ID-GadisNeural"),
    ("Indonesia Cowok - Tegas & Enerjik (Ardi)", "id-ID-ArdiNeural"),
    ("English Female - Cheerful (Emma)", "en-US-EmmaMultilingualNeural"),
    ("English Female - Expressive (Ava)", "en-US-AvaMultilingualNeural"),
    ("English Female (Jenny)", "en-US-JennyNeural"),
    ("English Male - Passionate (Guy)", "en-US-GuyNeural"),
    ("English Male - Warm (Andrew)", "en-US-AndrewMultilingualNeural"),
)

# (label, edge-tts rate, edge-tts pitch)
TTS_STYLE_PRESETS: dict[str, tuple[str, str, str]] = {
    "energetic": ("Bersemangat / Enerjik (Disarankan)", "+20%", "+12Hz"),
    "hype": ("Super Hype & Cepat (Viral)", "+26%", "+16Hz"),
    "calm": ("Santai / Kalem", "+8%", "+0Hz"),
}


def resolve_tts_prosody(
    tts_style: str = "energetic",
    rate: Optional[str] = None,
    pitch: Optional[str] = None,
) -> tuple[str, str]:
    """Return `(rate, pitch)` strings for edge-tts based on style preset and optional overrides."""
    _label, def_rate, def_pitch = TTS_STYLE_PRESETS.get(tts_style, TTS_STYLE_PRESETS["energetic"])
    eff_rate = (rate or "").strip() or def_rate
    eff_pitch = (pitch or "").strip() or def_pitch
    return eff_rate, eff_pitch


def _apply_energetic_punctuation(clean_text: str, pitch: str) -> str:
    """Add trailing exclamation inflection when using an energetic pitch unless already punctuated."""
    txt = clean_text.strip()
    if not txt:
        return txt
    p_norm = (pitch or "").strip().lower()
    if p_norm.startswith("+") and p_norm not in ("+0hz", "+0%"):
        if not txt.endswith(("!", "?")):
            txt = txt.rstrip(".") + "!"
    return txt


def _sapi_rate_from_percent(rate: str) -> int:
    """Map percentage rate string (e.g. '+20%') into Windows SAPI5 [-4..4] rate."""
    m = _PCT_RE.search(rate or "")
    if not m:
        return 2
    pct = int(m.group(1))
    if pct >= 24:
        return 3
    if pct >= 14:
        return 2
    if pct >= 4:
        return 1
    if pct <= -15:
        return -2
    return 0


def clean_title_for_speech(title: str) -> str:
    """Strip emojis, hashtags, URLs, and repeated punctuation so TTS reads naturally."""
    text = _URL_RE.sub(" ", title or "")
    text = _HASHTAG_RE.sub(" ", text)
    text = _ALLOWED_CHARS_RE.sub(" ", text)
    text = _MULTI_PUNCT_RE.sub(r"\1", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


async def _synthesize_edge_tts(
    clean_text: str,
    mp3_path: Path,
    voice: str,
    rate: str,
    pitch: str = "+12Hz",
) -> Path:
    import edge_tts

    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text=clean_text, voice=voice, rate=rate, pitch=pitch)
    await asyncio.wait_for(communicate.save(str(mp3_path)), timeout=20.0)
    if not mp3_path.is_file() or mp3_path.stat().st_size < 256:
        raise RuntimeError("Output file edge-tts kosong atau corrupt.")
    return mp3_path


async def _synthesize_sapi_fallback(clean_text: str, wav_path: Path, rate: str = "+20%") -> Path:
    if os.name != "nt":
        raise RuntimeError("Fallback SAPI5 hanya tersedia di Windows.")

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    text_b64 = base64.b64encode(clean_text.encode("utf-8")).decode("ascii")
    out_b64 = base64.b64encode(str(wav_path.resolve()).encode("utf-8")).decode("ascii")
    sapi_rate = _sapi_rate_from_percent(rate)

    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        f"$txt = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{text_b64}')); "
        f"$out = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{out_b64}')); "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Rate = {sapi_rate}; "
        "$s.SetOutputToWaveFile($out); "
        "$s.Speak($txt); "
        "$s.Dispose()"
    )
    rc, _, err = await run_subprocess_capture(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=15.0,
    )
    if rc != 0 or not wav_path.is_file() or wav_path.stat().st_size < 512:
        raise RuntimeError(f"SAPI5 fallback gagal (exit {rc}): {err}")
    return wav_path


async def _probe_audio_duration(
    audio_path: Path,
    clean_text: str,
    ffprobe_path: Optional[Path],
) -> float:
    if ffprobe_path is not None:
        rc, out, _ = await run_subprocess_capture(
            [
                str(ffprobe_path),
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(audio_path),
            ],
            timeout=10.0,
        )
        if rc == 0 and out:
            try:
                dur = float(out.strip())
                if dur > 0.1:
                    return dur
            except ValueError:
                pass

    words = max(1, len(clean_text.split()))
    return max(1.0, words * 0.42)


async def synthesize_opening_voiceover(
    text: str,
    output_audio_path: Path,
    voice: str,
    rate: str,
    ffprobe_path: Optional[Path],
    logger: JobLogger,
    pitch: str = "+12Hz",
) -> tuple[Optional[Path], float]:
    """Generate opening title voiceover audio and return `(audio_path, intro_duration_sec)`."""
    clean_text = clean_title_for_speech(text)
    if not clean_text:
        return None, 0.0

    spoken_text = _apply_energetic_punctuation(clean_text, pitch)
    mp3_path = output_audio_path.with_suffix(".mp3")
    wav_path = output_audio_path.with_suffix(".wav")
    final_path: Optional[Path] = None

    try:
        final_path = await _synthesize_edge_tts(
            spoken_text,
            mp3_path,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
        logger.info("cutter", f"Suara opening dibuat ({voice}, rate={rate}, pitch={pitch}): '{spoken_text}'")
    except Exception as exc:
        mp3_path.unlink(missing_ok=True)
        logger.warning("cutter", f"edge-tts gagal ({exc}); mencoba fallback SAPI5 lokal...")
        try:
            final_path = await _synthesize_sapi_fallback(spoken_text, wav_path, rate=rate)
            logger.info("cutter", f"Suara opening dibuat via SAPI5 offline: '{spoken_text}'")
        except Exception as sapi_exc:
            wav_path.unlink(missing_ok=True)
            logger.warning(
                "cutter",
                f"Fallback TTS SAPI5 juga gagal ({sapi_exc}); klip dirender tanpa suara opening.",
            )
            return None, 0.0

    raw_dur = await _probe_audio_duration(final_path, spoken_text, ffprobe_path)
    intro_dur = round(max(1.2, min(4.5, raw_dur + 0.2)), 3)
    return final_path, intro_dur
