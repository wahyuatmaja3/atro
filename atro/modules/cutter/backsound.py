"""Local background music scanning, selection, and FFmpeg sidechain-ducking audio mixing."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def scan_backsound_tracks(backsound_dir: Optional[Path]) -> list[Path]:
    """Return sorted list of valid audio files inside backsound_dir."""
    if backsound_dir is None or not backsound_dir.is_dir():
        return []
    tracks = [
        p.resolve()
        for p in backsound_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
    ]
    tracks.sort(key=lambda p: p.name.lower())
    return tracks


def pick_backsound_for_clip(tracks: list[Path], clip_index: int) -> Optional[Path]:
    """Deterministically rotate through available local backsound tracks per clip."""
    if not tracks:
        return None
    idx = max(0, clip_index - 1) % len(tracks)
    return tracks[idx]


def build_audio_filter_chain(
    has_source_audio: bool,
    has_backsound: bool,
    duration_sec: float,
    bgm_volume: float,
    enable_ducking: bool,
    enable_voice_polish: bool,
) -> tuple[list[str], Optional[str]]:
    """Build FFmpeg filter_complex audio lines and return `(lines, output_label)`."""
    dur = max(0.5, round(float(duration_sec), 3))
    vol = max(0.0, min(1.0, float(bgm_volume)))

    if not has_source_audio and not has_backsound:
        return [], None

    lines: list[str] = []

    if has_source_audio:
        voice_parts = ["aresample=async=1:first_pts=0"]
        if enable_voice_polish:
            voice_parts.append("highpass=f=80,dynaudnorm=f=150:g=15:p=0.90:m=5")
        voice_parts.append("aformat=sample_rates=48000:channel_layouts=stereo")
        voice_chain = ",".join(voice_parts)

        if not has_backsound:
            lines.append(f"[0:a]{voice_chain}[aout]")
            return lines, "aout"
        lines.append(f"[0:a]{voice_chain}[voice_clean]")

    if has_backsound:
        fade_in = round(min(1.2, dur * 0.25), 3)
        fade_out = round(min(1.5, dur * 0.30), 3)
        fade_out_start = max(0.0, round(dur - fade_out, 3))
        lines.append(
            f"[1:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={vol:.3f},"
            f"afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}[bgm_base]"
        )

        if has_source_audio:
            if enable_ducking:
                lines.append("[voice_clean]asplit=2[voice_main][voice_sc]")
                lines.append(
                    "[bgm_base][voice_sc]sidechaincompress=threshold=0.03:ratio=4:attack=25:release=350[bgm_duck]"
                )
                lines.append(
                    "[voice_main][bgm_duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
                    "alimiter=limit=0.95[aout]"
                )
            else:
                lines.append(
                    "[voice_clean][bgm_base]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
                    "alimiter=limit=0.95[aout]"
                )
        else:
            lines.append("[bgm_base]alimiter=limit=0.95[aout]")

    return lines, "aout"
