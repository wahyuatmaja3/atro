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
    transition_sfx_events: Optional[list[tuple[float, int, float]]] = None,
) -> tuple[list[str], Optional[str]]:
    """Build FFmpeg filter_complex audio lines and return `(lines, output_label)`."""
    dur_main = max(0.5, round(float(duration_sec), 3))
    dur_intro = max(0.0, round(float(intro_duration_sec), 3))
    has_intro = intro_tts_input_idx is not None and dur_intro > 0.0
    total_dur = round(dur_main + (dur_intro if has_intro else 0.0), 3)
    vol = max(0.0, min(1.0, float(bgm_volume)))
    valid_sfx = [
        (max(0.0, round(float(t), 3)), int(idx), max(0.0, min(1.0, float(sv))))
        for t, idx, sv in (transition_sfx_events or [])
        if 0.0 <= float(t) < max(0.05, total_dur - 0.05) and float(sv) > 0.005
    ]
    has_sfx = bool(valid_sfx)

    if not has_source_audio and not has_backsound and not has_intro and not has_sfx:
        return [], None

    lines: list[str] = []
    has_speech_stream = has_source_audio or has_intro
    final_base_lbl = "base_aout" if has_sfx else "aout"

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
                voice_parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
            voice_parts.append("aformat=sample_rates=48000:channel_layouts=stereo")
            voice_parts.append(f"atrim=0:{dur_main:.3f},asetpts=PTS-STARTPTS")
            lines.append(f"[0:a]{','.join(voice_parts)}[a_main]")
        else:
            lines.append(f"anullsrc=channel_layout=stereo:sample_rate=48000:d={dur_main:.3f}[a_main]")

        target_lbl = "voice_clean" if has_backsound else final_base_lbl
        lines.append(f"[a_intro][a_main]concat=n=2:v=0:a=1[{target_lbl}]")
    elif has_source_audio:
        voice_parts = ["aresample=async=1:first_pts=0"]
        if enable_voice_polish:
            voice_parts.append("highpass=f=80,dynaudnorm=f=150:g=15:p=0.90:m=5")
        if enable_loudnorm:
            voice_parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
        voice_parts.append("aformat=sample_rates=48000:channel_layouts=stereo")
        voice_chain = ",".join(voice_parts)

        if not has_backsound:
            lines.append(f"[0:a]{voice_chain}[{final_base_lbl}]")
        else:
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
                    f"[voice_main][bgm_duck]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
                    f"alimiter=limit=0.95[{final_base_lbl}]"
                )
            else:
                lines.append(
                    f"[voice_clean][bgm_base]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
                    f"alimiter=limit=0.95[{final_base_lbl}]"
                )
        else:
            lines.append(f"[bgm_base]alimiter=limit=0.95[{final_base_lbl}]")
    elif not has_speech_stream and has_sfx:
        lines.append(f"anullsrc=channel_layout=stereo:sample_rate=48000:d={total_dur:.3f}[{final_base_lbl}]")

    if has_sfx:
        # Group events by input index so shared WAV inputs get asplit cleanly
        counts_by_idx: dict[int, int] = {}
        for _, in_idx, _ in valid_sfx:
            counts_by_idx[in_idx] = counts_by_idx.get(in_idx, 0) + 1
        split_cursor: dict[int, int] = {}
        for in_idx, cnt in counts_by_idx.items():
            if cnt > 1:
                outs = "".join(f"[sfx_in_{in_idx}_{j}]" for j in range(cnt))
                lines.append(f"[{in_idx}:a]asplit={cnt}{outs}")
                split_cursor[in_idx] = 0

        sfx_lbls: list[str] = []
        for k, (ev_t, in_idx, sfx_vol) in enumerate(valid_sfx):
            if in_idx in split_cursor:
                slot = split_cursor[in_idx]
                split_cursor[in_idx] = slot + 1
                in_lbl = f"sfx_in_{in_idx}_{slot}"
            else:
                in_lbl = f"{in_idx}:a"
            delay_ms = max(0, int(round(ev_t * 1000.0)))
            out_sfx = f"sfx_ev_{k}"
            lines.append(
                f"[{in_lbl}]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
                f"volume={sfx_vol:.3f},adelay={delay_ms}|{delay_ms},"
                f"apad=whole_dur={total_dur:.3f},atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS[{out_sfx}]"
            )
            sfx_lbls.append(f"[{out_sfx}]")

        mix_inputs = f"[{final_base_lbl}]" + "".join(sfx_lbls)
        lines.append(
            f"{mix_inputs}amix=inputs={1 + len(sfx_lbls)}:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95[aout]"
        )

    return lines, "aout"
