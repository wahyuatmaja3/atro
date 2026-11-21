"""Local background music mixing, opening voiceover concatenation, and sidechain ducking."""

from __future__ import annotations

import random
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


def select_backsound_track(
    tracks: list[Path],
    mode: str,
    clip_index: int,
    selected_filename: Optional[str] = None,
) -> Optional[Path]:
    """Select a local backsound track using manual filename, random, or clip rotation."""
    if not tracks:
        return None
    if mode == "manual" and selected_filename:
        target_name = selected_filename.strip().lower()
        for t in tracks:
            if t.name.lower() == target_name:
                return t
    if mode == "random":
        return random.choice(tracks)
    return pick_backsound_for_clip(tracks, clip_index)


def build_audio_filter_chain(
    has_source_audio: bool,
    has_backsound: bool,
    duration_sec: float,
    bgm_volume: float,
    enable_ducking: bool,
    enable_voice_polish: bool,
    intro_tts_input_idx: Optional[int] = None,
    intro_duration_sec: float = 0.0,
    bgm_input_idx: int = 1,
    enable_loudnorm: bool = False,
) -> tuple[list[str], Optional[str]]:
    """Build FFmpeg filter_complex audio lines and return `(lines, output_label)`."""
    dur_main = max(0.5, round(float(duration_sec), 3))
    dur_intro = max(0.0, round(float(intro_duration_sec), 3))
    has_intro = intro_tts_input_idx is not None and dur_intro > 0.0
    total_dur = round(dur_main + (dur_intro if has_intro else 0.0), 3)
    vol = max(0.0, min(1.0, float(bgm_volume)))

    if not has_source_audio and not has_backsound and not has_intro:
        return [], None

    lines: list[str] = []
    has_speech_stream = has_source_audio or has_intro

    if has_intro:
        lines.append(
            f"[{intro_tts_input_idx}:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
            f"silenceremove=start_periods=1:start_threshold=-40dB,"
            f"apad=whole_dur={dur_intro:.3f},atrim=0:{dur_intro:.3f},asetpts=PTS-STARTPTS,"
            f"dynaudnorm=f=100:g=11:p=0.92:m=4[a_intro]"
        )
        if has_source_audio:
            voice_parts = ["aresample=async=1:first_pts=0"]
            if enable_voice_polish:
                voice_parts.append("highpass=f=80,dynaudnorm=f=150:g=15:p=0.90:m=5")
            if enable_loudnorm:
                voice_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
            voice_parts.append("aformat=sample_rates=48000:channel_layouts=stereo")
            voice_parts.append(f"atrim=0:{dur_main:.3f},asetpts=PTS-STARTPTS")
            lines.append(f"[0:a]{','.join(voice_parts)}[a_main]")
        else:
            lines.append(f"anullsrc=channel_layout=stereo:sample_rate=48000:d={dur_main:.3f}[a_main]")

        target_lbl = "voice_clean" if has_backsound else "aout"
        lines.append(f"[a_intro][a_main]concat=n=2:v=0:a=1[{target_lbl}]")
        if not has_backsound:
            return lines, "aout"
    elif has_source_audio:
        voice_parts = ["aresample=async=1:first_pts=0"]
        if enable_voice_polish:
            voice_parts.append("highpass=f=80,dynaudnorm=f=150:g=15:p=0.90:m=5")
        if enable_loudnorm:
            voice_parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        voice_parts.append("aformat=sample_rates=48000:channel_layouts=stereo")
        voice_chain = ",".join(voice_parts)

        if not has_backsound:
            lines.append(f"[0:a]{voice_chain}[aout]")
            return lines, "aout"
        lines.append(f"[0:a]{voice_chain}[voice_clean]")

    if has_backsound:
        fade_in = round(min(1.2, total_dur * 0.25), 3)
        fade_out = round(min(1.5, total_dur * 0.30), 3)
        fade_out_start = max(0.0, round(total_dur - fade_out, 3))
        lines.append(
            f"[{bgm_input_idx}:a]atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={vol:.3f},"
            f"afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f}[bgm_base]"
        )

        if has_speech_stream:
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
