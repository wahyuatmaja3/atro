"""Dynamic viral ASS subtitle and hook-title generator for Podcast and Game formats."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from atro.core.schemas import AspectRatio, EditFormat, TranscriptSegment


@dataclass(slots=True)
class CaptionWord:
    """Single word with clip-local timing."""

    text: str
    start: float
    end: float


@dataclass(slots=True)
class CaptionChunk:
    """Short 3-4 word viral caption phrase."""

    words: list[CaptionWord]
    start: float
    end: float


def _format_ass_time(sec: float) -> str:
    clamped = max(0.0, float(sec))
    total_cs = int(round(clamped * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")


def _wrap_hook_title(title: str, max_chars: int = 28) -> str:
    cleaned = " ".join(title.strip().split())
    if not cleaned:
        return ""
    wrapped_lines = textwrap.wrap(cleaned, width=max_chars)
    if len(wrapped_lines) > 3:
        wrapped_lines = wrapped_lines[:3]
        wrapped_lines[-1] = wrapped_lines[-1][: max(1, max_chars - 3)].rstrip() + "..."
    return "\\N".join(_escape_ass_text(line) for line in wrapped_lines)


def build_viral_caption_chunks(
    segments: list[TranscriptSegment],
    clip_start: float,
    clip_end: float,
    max_words_per_chunk: int = 4,
) -> list[CaptionChunk]:
    """Slice overlapping transcript segments into clip-local 3-4 word viral caption chunks."""
    clip_dur = max(0.0, round(clip_end - clip_start, 3))
    if clip_dur <= 0.0 or not segments:
        return []

    max_words = max(1, int(max_words_per_chunk))
    chunks: list[CaptionChunk] = []

    for seg in segments:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue
        rel_start = max(0.0, seg.start - clip_start)
        rel_end = min(clip_dur, seg.end - clip_start)
        if rel_end - rel_start < 0.08:
            continue

        raw_words = [w for w in seg.text.strip().split() if w]
        if not raw_words:
            continue

        weights = [max(1, len(w)) for w in raw_words]
        total_weight = sum(weights)
        seg_span = rel_end - rel_start

        word_timings: list[CaptionWord] = []
        cursor = rel_start
        for idx, (word, weight) in enumerate(zip(raw_words, weights)):
            if idx == len(raw_words) - 1:
                w_end = rel_end
            else:
                w_dur = seg_span * (weight / total_weight)
                w_end = min(rel_end, cursor + max(0.05, w_dur))
            if w_end <= cursor:
                w_end = min(rel_end, cursor + 0.05)
            word_timings.append(CaptionWord(text=word, start=round(cursor, 3), end=round(w_end, 3)))
            cursor = w_end

        groups: list[list[CaptionWord]] = []
        i = 0
        n = len(word_timings)
        while i < n:
            remaining = n - i
            if remaining == max_words + 1 and max_words >= 3:
                take = max_words - 1
            else:
                take = min(max_words, remaining)
            groups.append(word_timings[i : i + take])
            i += take

        for grp in groups:
            c_start = grp[0].start
            c_end = max(c_start + 0.08, grp[-1].end)
            chunks.append(CaptionChunk(words=grp, start=round(c_start, 3), end=round(min(clip_dur, c_end), 3)))

    return chunks


def generate_clip_ass_file(
    output_ass_path: Path,
    aspect_ratio: AspectRatio,
    clip_duration_sec: float,
    clip_title: str,
    segments: list[TranscriptSegment],
    clip_start_sec: float,
    clip_end_sec: float,
    enable_subtitles: bool,
    enable_hook_title: bool,
    edit_format: EditFormat = "podcast",
    opening_duration_sec: float = 0.0,
    source_credit: str = "",
) -> Optional[Path]:
    """Generate an ASS subtitle/hook-title file tailored to Podcast or Game Shorts format."""
    if not enable_subtitles and not enable_hook_title:
        return None

    is_vertical = aspect_ratio == "9:16"
    is_game = edit_format == "game"
    play_res_x, play_res_y = (1080, 1920) if is_vertical else (1920, 1080)
    intro_shift = max(0.0, float(opening_duration_sec))

    # Format-specific typography, colors (ASS BGR), and safe-zone placement
    sub_font_size = 64 if is_vertical else 50
    if is_game:
        sub_margin_v = 330 if is_vertical else 85
        highlight_bgr = "&H00F8BD38&"  # Electric cyan (#38BDF8)
    else:
        sub_margin_v = 370 if is_vertical else 85
        highlight_bgr = "&H0015CCFA&"  # Viral yellow (#FACC15)

    hook_font_size = 54 if is_vertical else 44
    if is_game:
        # Modern Crisp Light Card (white pill card, black bold text)
        hook_primary = "&H00170F0F"
        hook_box = "&H08F8FAFC"
        hook_outline_w = 18
        if intro_shift > 0.0:
            hook_align = 5
            hook_margin_v = 0
        elif is_vertical:
            hook_align = 8
            hook_margin_v = 690
        else:
            hook_align = 8
            hook_margin_v = 60
    else:
        # Modern Dark Glass Card (dark translucent box, crisp white bold text)
        hook_primary = "&H00FFFFFF"
        hook_box = "&H5517110F"
        hook_outline_w = 16
        if intro_shift > 0.0:
            hook_align = 5
            hook_margin_v = 0
        elif is_vertical:
            hook_align = 8
            hook_margin_v = 175
        else:
            hook_align = 8
            hook_margin_v = 60

    dialogue_lines: list[str] = []

    if enable_hook_title and clip_title.strip() and clip_duration_sec >= 0.8:
        if intro_shift > 0.0:
            hook_start = 0.05
            hook_end = max(0.8, round(intro_shift - 0.05, 3))
            anim_tag = "{\\fscx90\\fscy90\\t(0,110,\\fscx100\\fscy100)\\fad(120,220)}"
        else:
            hook_start = 0.15
            hook_end = min(3.8, max(0.8, round(clip_duration_sec - 0.2, 3)))
            anim_tag = (
                "{\\fscx90\\fscy90\\t(0,110,\\fscx100\\fscy100)\\fad(120,220)}"
                if is_game
                else "{\\fad(220,350)}"
            )
        wrapped_title = _wrap_hook_title(clip_title, max_chars=26 if is_vertical else 34)
        if wrapped_title:
            dialogue_lines.append(
                f"Dialogue: 2,{_format_ass_time(hook_start)},{_format_ass_time(hook_end)},"
                f"HookTitle,,0,0,0,,{anim_tag}{wrapped_title}"
            )

    clean_credit = source_credit.strip()
    if is_game and clean_credit and clean_credit.lower() != "local file":
        handle = clean_credit if clean_credit.startswith("@") else f"@{clean_credit}"
        credit_text = _escape_ass_text(f"Source: {handle}")
        dialogue_lines.append(
            f"Dialogue: 0,{_format_ass_time(0.0)},{_format_ass_time(clip_duration_sec)},"
            f"SourceCredit,,0,0,0,,{credit_text}"
        )

    if enable_subtitles and segments:
        chunks = build_viral_caption_chunks(
            segments=segments,
            clip_start=clip_start_sec,
            clip_end=clip_end_sec,
            max_words_per_chunk=4,
        )
        for chunk in chunks:
            for active_idx, word_obj in enumerate(chunk.words):
                ev_start = round(word_obj.start + intro_shift, 3)
                raw_end = (
                    chunk.words[active_idx + 1].start
                    if active_idx + 1 < len(chunk.words)
                    else chunk.end
                )
                ev_end = min(clip_duration_sec, round(raw_end + intro_shift, 3))
                if ev_end <= ev_start:
                    ev_end = min(clip_duration_sec, ev_start + 0.08)
                if ev_end <= ev_start:
                    continue

                rendered_words: list[str] = []
                for j, w in enumerate(chunk.words):
                    upper_esc = _escape_ass_text(w.text.upper())
                    if j == active_idx:
                        rendered_words.append(
                            f"{{\\c{highlight_bgr}\\fscx108\\fscy108}}"
                            + upper_esc
                            + "{\\c&H00FFFFFF&\\fscx100\\fscy100}"
                        )
                    else:
                        rendered_words.append(upper_esc)

                prefix = "{\\fscx92\\fscy92\\t(0,80,\\fscx100\\fscy100)}" if active_idx == 0 else ""
                line_text = prefix + " ".join(rendered_words)
                dialogue_lines.append(
                    f"Dialogue: 1,{_format_ass_time(ev_start)},{_format_ass_time(ev_end)},"
                    f"ViralSub,,0,0,0,,{line_text}"
                )

    if not dialogue_lines:
        return None

    credit_margin_v = 792 if is_vertical else 24
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {play_res_x}",
            f"PlayResY: {play_res_y}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 1",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: ViralSub,Arial Black,{sub_font_size},&H00FFFFFF,{highlight_bgr},&H00000000,&H80000000,"
            f"-1,0,0,0,100,100,0,0,1,5,2,2,48,48,{sub_margin_v},1",
            f"Style: HookTitle,Arial Black,{hook_font_size},{hook_primary},{hook_primary},{hook_box},{hook_box},"
            f"-1,0,0,0,100,100,0,0,3,{hook_outline_w},0,{hook_align},60,60,{hook_margin_v},1",
            f"Style: SourceCredit,Arial,24,&HB0FFFFFF,&HB0FFFFFF,&H80000000,&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,2,1,7,28,28,{credit_margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *dialogue_lines,
            "",
        ]
    )

    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    output_ass_path.write_text(header, encoding="utf-8")
    return output_ass_path
