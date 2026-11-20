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
) -> Path:
    import edge_tts

    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text=clean_text, voice=voice, rate=rate)
    await asyncio.wait_for(communicate.save(str(mp3_path)), timeout=20.0)
    if not mp3_path.is_file() or mp3_path.stat().st_size < 256:
        raise RuntimeError("Output file edge-tts kosong atau corrupt.")
    return mp3_path


async def _synthesize_sapi_fallback(clean_text: str, wav_path: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError("Fallback SAPI5 hanya tersedia di Windows.")

    wav_path.parent.mkdir(parents=True, exist_ok=True)
    text_b64 = base64.b64encode(clean_text.encode("utf-8")).decode("ascii")
    out_b64 = base64.b64encode(str(wav_path.resolve()).encode("utf-8")).decode("ascii")

    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        f"$txt = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{text_b64}')); "
        f"$out = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{out_b64}')); "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 1; "
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
) -> tuple[Optional[Path], float]:
    """Generate opening title voiceover audio and return `(audio_path, intro_duration_sec)`."""
    clean_text = clean_title_for_speech(text)
    if not clean_text:
        return None, 0.0

    mp3_path = output_audio_path.with_suffix(".mp3")
    wav_path = output_audio_path.with_suffix(".wav")
    final_path: Optional[Path] = None

    try:
        final_path = await _synthesize_edge_tts(clean_text, mp3_path, voice=voice, rate=rate)
        logger.info("cutter", f"Suara opening dibuat ({voice}): '{clean_text}'")
    except Exception as exc:
        mp3_path.unlink(missing_ok=True)
        logger.warning("cutter", f"edge-tts gagal ({exc}); mencoba fallback SAPI5 lokal...")
        try:
            final_path = await _synthesize_sapi_fallback(clean_text, wav_path)
            logger.info("cutter", f"Suara opening dibuat via SAPI5 offline: '{clean_text}'")
        except Exception as sapi_exc:
            wav_path.unlink(missing_ok=True)
            logger.warning(
                "cutter",
                f"Fallback TTS SAPI5 juga gagal ({sapi_exc}); klip dirender tanpa suara opening.",
            )
            return None, 0.0

    raw_dur = await _probe_audio_duration(final_path, clean_text, ffprobe_path)
    intro_dur = round(max(1.2, min(4.5, raw_dur + 0.2)), 3)
    return final_path, intro_dur
