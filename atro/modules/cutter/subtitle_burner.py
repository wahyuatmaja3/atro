"""Dynamic viral ASS subtitle and hook-title generator for Podcast and Game formats."""

from __future__ import annotations

import re
import struct
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics

from atro.core.composer_schema import ClipComposerConfig
from atro.core.schemas import AspectRatio, EditFormat, TranscriptSegment, TranscriptWord

_ACTION_TOKEN_RE = re.compile(r"^(?:\*[^*]+\*|\[[^\]]+\])$")
_LOADED_FONT_FAMILIES: dict[str, str] = {}
_QT_APP_HOLDER: Optional[QCoreApplication] = None

_EMOJI_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("laugh", "laughs", "tertawa", "ngakak", "kocak", "lucu", "haha", "wkwk", "funny", "joke"), "😂"),
    (("uang", "duit", "cuan", "juta", "miliar", "triliun", "money", "rich", "cash", "profit", "modal", "bisnis", "omset", "mahal", "bayar"), "💰"),
    (("fire", "gila", "parah", "insane", "crazy", "viral", "mantap", "keren", "gacor", "panas", "api", "menyala"), "🔥"),
    (("shock", "kaget", "serius", "serem", "omg", "wtf", "gokil", "wah", "wow", "kacau", "bahaya", "aneh"), "😱"),
    (("ide", "tips", "rahasia", "cara", "belajar", "ilmu", "otak", "mindset", "pikir", "smart", "strategi", "trik"), "💡"),
    (("cepat", "gas", "naik", "sukses", "target", "rocket", "win", "menang", "juara", "clutch", "booyah", "gg"), "🚀"),
    (("penting", "fokus", "warning", "jangan", "stop", "wajib", "harus", "inget", "catat", "awas"), "⚡"),
    (("cinta", "sayang", "hati", "baper", "suka", "love", "rindu", "sedih", "nangis"), "❤️"),
)

_STOPWORDS_DUAL_KEYWORD: frozenset[str] = frozenset({
    "yang", "dan", "ini", "itu", "di", "ke", "dari", "untuk", "dengan", "pada",
    "adalah", "akan", "bisa", "juga", "atau", "karena", "jadi", "kalau", "kalo",
    "ya", "yg", "gak", "ga", "nggak", "tidak", "aja", "nih", "sih", "dong",
    "aku", "kamu", "kita", "kami", "mereka", "dia", "lu", "gue", "gw", "bang",
    "the", "and", "that", "this", "with", "for", "from", "have", "what", "just",
    "are", "was", "were", "you", "your", "they", "them", "she", "him", "her",
})


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
    normalized = (
        (title or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\\n", "\n")
        .strip()
    )
    if not normalized:
        return ""
    wrapped_lines: list[str] = []
    for para in normalized.split("\n"):
        cleaned_para = " ".join(para.split())
        if not cleaned_para:
            continue
        wrapped_lines.extend(textwrap.wrap(cleaned_para, width=max_chars))
    if not wrapped_lines:
        return ""
    if len(wrapped_lines) > 4:
        wrapped_lines = wrapped_lines[:4]
        wrapped_lines[-1] = wrapped_lines[-1][: max(1, max_chars - 3)].rstrip() + "..."
    return "\\N".join(_escape_ass_text(line) for line in wrapped_lines)


def _compute_word_timings_for_segment(
    seg_start: float,
    seg_end: float,
    seg_text: str,
    seg_words: list[TranscriptWord],
    clip_start: float,
    clip_dur: float,
    timing_offset_sec: float = 0.0,
) -> list[CaptionWord]:
    """Compute clip-local word timings using exact word timestamps when available, or pause-weighted fallback."""
    raw_words = [w for w in seg_text.strip().split() if w]
    if not raw_words or clip_dur <= 0.0:
        return []

    offset = float(timing_offset_sec)
    # Use exact acoustic word timestamps whenever word count aligns (even if user fixed typos)
    if seg_words and len(seg_words) == len(raw_words):
        out: list[CaptionWord] = []
        for tok, w_obj in zip(raw_words, seg_words):
            ws = w_obj.start - clip_start + offset
            we = w_obj.end - clip_start + offset
            if we <= 0.0 or ws >= clip_dur:
                continue
            ws_clamped = max(0.0, ws)
            we_clamped = min(clip_dur, max(ws_clamped + 0.04, we))
            if we_clamped <= ws_clamped:
                continue
            out.append(
                CaptionWord(
                    text=tok,
                    start=round(ws_clamped, 3),
                    end=round(we_clamped, 3),
                )
            )
        if out:
            return out

    rel_start = max(0.0, seg_start - clip_start + offset)
    rel_end = min(clip_dur, seg_end - clip_start + offset)
    if rel_end - rel_start < 0.08:
        return []

    seg_span = rel_end - rel_start

    # Reserve post-punctuation silence so words after commas/periods do not appear early
    pauses: list[float] = []
    for idx, word in enumerate(raw_words):
        if idx == len(raw_words) - 1:
            pauses.append(0.0)
        elif word.endswith((".", "!", "?")):
            pauses.append(0.18)
        elif word.endswith((",", ";", ":")):
            pauses.append(0.10)
        else:
            pauses.append(0.0)

    raw_pause_total = sum(pauses)
    max_pause_budget = seg_span * 0.35
    pause_scale = (max_pause_budget / raw_pause_total) if raw_pause_total > max_pause_budget and raw_pause_total > 0 else 1.0
    pauses = [p * pause_scale for p in pauses]
    speech_span = max(0.05 * len(raw_words), seg_span - sum(pauses))

    weights = [max(2, len(w)) for w in raw_words]
    total_weight = sum(weights)

    word_timings: list[CaptionWord] = []
    cursor = rel_start
    for idx, (word, weight, pause_after) in enumerate(zip(raw_words, weights, pauses)):
        if idx == len(raw_words) - 1:
            w_end = rel_end
        else:
            w_dur = speech_span * (weight / total_weight)
            w_end = min(rel_end, cursor + max(0.05, w_dur))
        if w_end <= cursor:
            w_end = min(rel_end, cursor + 0.05)
        word_timings.append(CaptionWord(text=word, start=round(cursor, 3), end=round(w_end, 3)))
        cursor = min(rel_end, w_end + pause_after)

    return word_timings


def _group_words_pause_aware(
    word_timings: list[CaptionWord],
    max_words: int,
    pause_break_sec: float = 0.22,
) -> list[list[CaptionWord]]:
    """Split word list into chunks respecting silence gaps and sentence/clause punctuation."""
    if not word_timings:
        return []

    runs: list[list[CaptionWord]] = [[word_timings[0]]]
    for w in word_timings[1:]:
        prev = runs[-1][-1]
        gap = w.start - prev.end
        if gap >= pause_break_sec or prev.text.endswith((".", "!", "?", ",", ";", ":")):
            runs.append([w])
        else:
            runs[-1].append(w)

    groups: list[list[CaptionWord]] = []
    for run in runs:
        i = 0
        n = len(run)
        while i < n:
            remaining = n - i
            if remaining == max_words + 1 and max_words >= 3:
                take = max_words - 1
            else:
                take = min(max_words, remaining)
            groups.append(run[i : i + take])
            i += take
    return groups


def build_viral_caption_chunks(
    segments: list[TranscriptSegment],
    clip_start: float,
    clip_end: float,
    max_words_per_chunk: int = 4,
) -> list[CaptionChunk]:
    """Slice overlapping transcript segments into clip-local pause-aware viral caption chunks."""
    clip_dur = max(0.0, round(clip_end - clip_start, 3))
    if clip_dur <= 0.0 or not segments:
        return []

    max_words = max(1, int(max_words_per_chunk))
    chunks: list[CaptionChunk] = []

    for seg in segments:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue
        word_timings = _compute_word_timings_for_segment(
            seg_start=seg.start,
            seg_end=seg.end,
            seg_text=seg.text,
            seg_words=seg.words,
            clip_start=clip_start,
            clip_dur=clip_dur,
        )
        if not word_timings:
            continue

        for grp in _group_words_pause_aware(word_timings, max_words=max_words):
            c_start = grp[0].start
            c_end = max(c_start + 0.08, grp[-1].end)
            chunks.append(
                CaptionChunk(
                    words=grp,
                    start=round(c_start, 3),
                    end=round(min(clip_dur, c_end), 3),
                )
            )

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


@dataclass(slots=True)
class ComposerCaptionChunk:
    """Word-level caption chunk carrying speaker identity for composer preview and ASS."""

    words: list[CaptionWord]
    start: float
    end: float
    speaker: str = "SPEAKER_1"


def is_action_token(word: str) -> bool:
    """Return True for non-speech action markers such as *LAUGHS* or [TERTAWA]."""
    cleaned = word.strip(".,!?;:\"'")
    return bool(cleaned and _ACTION_TOKEN_RE.match(cleaned))


def _parse_hex_rgba(hex_color: str, default_alpha: int = 0) -> tuple[int, str, str, str]:
    """Return `(ass_alpha_int, rr, gg, bb)` from `#RRGGBB` or `#AARRGGBB`."""
    raw = (hex_color or "").strip().lstrip("#")
    if len(raw) == 8:
        try:
            qt_alpha = int(raw[0:2], 16)
            ass_alpha = max(0, min(255, 255 - qt_alpha))
            return ass_alpha, raw[2:4].upper(), raw[4:6].upper(), raw[6:8].upper()
        except ValueError:
            pass
    elif len(raw) == 6:
        try:
            int(raw, 16)
            return max(0, min(255, default_alpha)), raw[0:2].upper(), raw[2:4].upper(), raw[4:6].upper()
        except ValueError:
            pass
    return max(0, min(255, default_alpha)), "FF", "FF", "FF"


def hex_to_ass_color(
    hex_color: str,
    default_alpha: int = 0,
    trailing_amp: bool = True,
) -> str:
    """Convert #RRGGBB or #AARRGGBB (Qt alpha: FF=opaque) into ASS &HAABBGGRR(&) (00=opaque)."""
    ass_alpha, rr, gg, bb = _parse_hex_rgba(hex_color, default_alpha=default_alpha)
    suffix = "&" if trailing_amp else ""
    return f"&H{ass_alpha:02X}{bb}{gg}{rr}{suffix}"


def _hex_to_ass_rgb_and_alpha(hex_color: str, default_alpha: int = 0) -> tuple[str, str]:
    """Return `('&HBBGGRR&', '&HAA&')` for inline ASS drawing/text alpha tags."""
    ass_alpha, rr, gg, bb = _parse_hex_rgba(hex_color, default_alpha=default_alpha)
    return f"&H{bb}{gg}{rr}&", f"&H{ass_alpha:02X}&"


def ensure_qt_gui_app() -> None:
    """Ensure a QApplication exists so QImage/QPainter/QFontMetrics work headlessly."""
    global _QT_APP_HOLDER
    if QCoreApplication.instance() is None:
        from PyQt6.QtWidgets import QApplication

        _QT_APP_HOLDER = QApplication([])


def qcolor_from_hex(hex_str: str, fallback: str = "#FFFFFF") -> QColor:
    """Parse #RRGGBB or #AARRGGBB hex string into QColor safely."""
    raw = (hex_str or "").strip()
    if not raw.startswith("#"):
        raw = "#" + raw
    col = QColor(raw)
    if not col.isValid():
        return QColor(fallback)
    return col


def register_qt_font(custom_font_path: Optional[str], fallback_family: str) -> str:
    """Register custom .ttf/.otf in QFontDatabase once and return resolved family name."""
    ensure_qt_gui_app()
    if custom_font_path:
        path = Path(custom_font_path)
        if path.is_file():
            key = str(path.resolve())
            if key in _LOADED_FONT_FAMILIES:
                return _LOADED_FONT_FAMILIES[key]
            font_id = QFontDatabase.addApplicationFont(key)
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    _LOADED_FONT_FAMILIES[key] = families[0]
                    return families[0]
            resolved = resolve_font_family(key, fallback_family)
            _LOADED_FONT_FAMILIES[key] = resolved
            return resolved
    return (fallback_family or "Arial Black").strip() or "Arial Black"


def pick_contextual_emoji(text: str) -> str:
    """Select a high-impact viral emoji matching the spoken caption line."""
    normalized = re.sub(r"[^\w\s]", " ", (text or "").lower())
    tokens = set(normalized.split())
    if not tokens:
        return ""
    for keywords, emoji in _EMOJI_RULES:
        if any(kw in tokens or kw in normalized for kw in keywords):
            return emoji
    return ""


def pick_emphasis_word_index(words: list[str], active_idx: int) -> int:
    """Choose a secondary emphasis keyword index for Hormozi-style dual coloring."""
    if len(words) < 3:
        return -1
    best_idx = -1
    best_score = 0
    for idx, raw in enumerate(words):
        if idx == active_idx or is_action_token(raw):
            continue
        cleaned = re.sub(r"[^\w]", "", raw).lower()
        if len(cleaned) < 3 or cleaned in _STOPWORDS_DUAL_KEYWORD:
            continue
        score = len(cleaned) + (4 if any(ch.isdigit() for ch in cleaned) else 0)
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx


@dataclass(slots=True)
class WordBoxMetrics:
    """Measured canvas-space geometry for one rendered word in a caption line."""

    text: str
    raw_text: str
    local_x: float
    width: float
    center_canvas_x: float
    is_active: bool
    is_action: bool
    is_secondary_keyword: bool


@dataclass(slots=True)
class CaptionLineLayout:
    """Shared WYSIWYG geometry consumed by both Preview QPainter and ASS Layer 1/2."""

    font: QFont
    fm: QFontMetrics
    scale_fit: float
    total_unscaled_w: float
    baseline_y: float
    word_boxes: list[WordBoxMetrics]
    secondary_idx: int
    box_center_x: float
    box_center_y: float
    box_w: float
    box_h: float
    pill_center_x: float
    pill_center_y: float
    pill_w: float
    pill_h: float
    emoji: str
    emoji_y: float


def compute_caption_line_layout(
    config: ClipComposerConfig,
    raw_words: list[str],
    active_idx: int,
    canvas_w: int,
    canvas_h: int,
) -> CaptionLineLayout:
    """Compute identical word positions, full-line box, active pill box, and emoji anchor."""
    subs = config.subtitles
    family = register_qt_font(subs.custom_font_path, subs.font_family)
    font_px = max(16, int(subs.font_size))
    font = QFont(family, font_px)
    font.setBold(True)
    fm = QFontMetrics(font)
    space_w = float(fm.horizontalAdvance(" "))

    display_words = [w.upper() if subs.uppercase else w for w in raw_words]
    widths = [float(fm.horizontalAdvance(txt)) for txt in display_words]
    total_w = sum(widths) + (space_w * max(0, len(widths) - 1))
    max_avail_w = max(120, canvas_w - 80)
    scale_fit = min(1.0, max_avail_w / max(1.0, total_w))

    sec_idx = (
        pick_emphasis_word_index(raw_words, active_idx)
        if subs.dual_keyword_enabled
        else -1
    )

    anchor_x = float(subs.pos_x)
    anchor_y = float(subs.pos_y)
    baseline_y = (fm.ascent() - fm.descent()) * 0.5

    word_boxes: list[WordBoxMetrics] = []
    cursor_x = -total_w * 0.5
    for idx, (txt, raw_tok, ww) in enumerate(zip(display_words, raw_words, widths)):
        center_canvas_x = anchor_x + (cursor_x + ww * 0.5) * scale_fit
        word_boxes.append(
            WordBoxMetrics(
                text=txt,
                raw_text=raw_tok,
                local_x=cursor_x,
                width=ww,
                center_canvas_x=center_canvas_x,
                is_active=(idx == active_idx),
                is_action=is_action_token(raw_tok),
                is_secondary_keyword=(idx == sec_idx),
            )
        )
        cursor_x += ww + space_w

    line_h = float(fm.height()) * scale_fit
    box_w = total_w * scale_fit + max(32.0, font_px * 0.65)
    box_h = line_h + max(18.0, font_px * 0.36)

    w_scale = 1.12 if subs.animation == "pop" else (1.16 if subs.animation == "scale" else 1.0)
    if 0 <= active_idx < len(word_boxes):
        act_box = word_boxes[active_idx]
        pill_cx = act_box.center_canvas_x
        pill_w = act_box.width * w_scale * scale_fit + max(22.0, font_px * 0.44)
        pill_h = line_h * w_scale + max(12.0, font_px * 0.24)
    else:
        pill_cx = anchor_x
        pill_w = box_w
        pill_h = box_h

    emoji = pick_contextual_emoji(" ".join(raw_words)) if subs.auto_emoji_enabled else ""
    emoji_y = float(max(72, min(canvas_h - 60, int(round(anchor_y - box_h * 0.5 - font_px * 0.52)))))

    return CaptionLineLayout(
        font=font,
        fm=fm,
        scale_fit=scale_fit,
        total_unscaled_w=total_w,
        baseline_y=baseline_y,
        word_boxes=word_boxes,
        secondary_idx=sec_idx,
        box_center_x=anchor_x,
        box_center_y=anchor_y,
        box_w=box_w,
        box_h=box_h,
        pill_center_x=pill_cx,
        pill_center_y=anchor_y,
        pill_w=pill_w,
        pill_h=pill_h,
        emoji=emoji,
        emoji_y=emoji_y,
    )


def _ass_rounded_rect_drawing(width: float, height: float, radius: float) -> str:
    """Return libass \\p1 cubic-Bezier rounded rectangle centered via \\an5\\pos(cx,cy)."""
    w = max(8.0, float(width))
    h = max(8.0, float(height))
    r = max(0.0, min(float(radius), w * 0.45, h * 0.45))
    if r < 1.0:
        return f"m 0 0 l {w:.1f} 0 l {w:.1f} {h:.1f} l 0 {h:.1f}"
    c = r * (1.0 - 0.55228475)
    return (
        f"m {r:.1f} 0 "
        f"l {w - r:.1f} 0 "
        f"b {w - c:.1f} 0 {w:.1f} {c:.1f} {w:.1f} {r:.1f} "
        f"l {w:.1f} {h - r:.1f} "
        f"b {w:.1f} {h - c:.1f} {w - c:.1f} {h:.1f} {w - r:.1f} {h:.1f} "
        f"l {r:.1f} {h:.1f} "
        f"b {c:.1f} {h:.1f} 0 {h - c:.1f} 0 {h - r:.1f} "
        f"l 0 {r:.1f} "
        f"b 0 {c:.1f} {c:.1f} 0 {r:.1f} 0"
    )


def _read_ttf_family_name(font_path: Path) -> Optional[str]:
    """Parse TrueType/OpenType 'name' table in pure Python to extract font family name."""
    try:
        data = font_path.read_bytes()
        if len(data) < 12:
            return None
        num_tables = struct.unpack(">H", data[4:6])[0]
        name_offset = None
        for i in range(num_tables):
            rec_start = 12 + i * 16
            if rec_start + 16 > len(data):
                break
            tag = data[rec_start : rec_start + 4]
            if tag == b"name":
                name_offset = struct.unpack(">I", data[rec_start + 8 : rec_start + 12])[0]
                break
        if name_offset is None or name_offset + 6 > len(data):
            return None
        count, str_offset = struct.unpack(">HH", data[name_offset + 2 : name_offset + 6])
        storage_start = name_offset + str_offset
        preferred: dict[int, str] = {}
        for i in range(count):
            rec = name_offset + 6 + i * 12
            if rec + 12 > len(data):
                break
            platform_id, _enc_id, _lang_id, name_id, length, offset = struct.unpack(
                ">HHHHHH", data[rec : rec + 12]
            )
            if name_id not in (1, 16):
                continue
            val_start = storage_start + offset
            val_end = val_start + length
            if val_end > len(data):
                continue
            raw_bytes = data[val_start:val_end]
            encoding = "utf-16-be" if platform_id in (0, 3) else "latin-1"
            try:
                decoded = raw_bytes.decode(encoding).strip("\x00").strip()
            except Exception:
                continue
            if decoded:
                preferred[name_id] = decoded
        return preferred.get(16) or preferred.get(1)
    except Exception:
        return None


def resolve_font_family(custom_font_path: Optional[str], fallback: str = "Arial Black") -> str:
    """Resolve display/libass font family name from a custom .ttf/.otf file when available."""
    if custom_font_path:
        path = Path(custom_font_path)
        if path.is_file():
            parsed = _read_ttf_family_name(path)
            if parsed:
                return parsed
            return path.stem.replace("-", " ").replace("_", " ").strip() or fallback
    return (fallback or "Arial Black").strip() or "Arial Black"


def build_composer_caption_chunks(
    config: ClipComposerConfig,
    segments: list[TranscriptSegment],
    clip_start_sec: Optional[float] = None,
    clip_end_sec: Optional[float] = None,
) -> list[ComposerCaptionChunk]:
    """Build speaker-aware word-level caption chunks driven by ClipComposerConfig."""
    pad = max(0.0, float(config.timing.padding_sec))
    clip_start = (
        float(clip_start_sec)
        if clip_start_sec is not None
        else max(0.0, float(config.timing.start_time) - pad)
    )
    clip_end = (
        float(clip_end_sec)
        if clip_end_sec is not None
        else float(config.timing.end_time) + pad
    )
    clip_dur = max(0.0, round(clip_end - clip_start, 3))
    if clip_dur <= 0.0:
        return []

    if config.transcript_overrides:
        source_items = [
            (ov.start, ov.end, ov.text, ov.speaker, ov.words)
            for ov in config.transcript_overrides
        ]
    else:
        source_items = [
            (seg.start, seg.end, seg.text, "SPEAKER_1", seg.words)
            for seg in segments
        ]

    if not source_items:
        return []

    max_words = max(1, int(config.subtitles.max_words_per_line))
    if config.subtitles.style_mode == "kinetic_1_2":
        max_words = min(max_words, 2)

    offset_sec = float(config.subtitles.timing_offset_sec)
    chunks: list[ComposerCaptionChunk] = []
    for seg_start, seg_end, seg_text, speaker, seg_words in source_items:
        if seg_end + offset_sec <= clip_start or seg_start + offset_sec >= clip_end:
            continue
        word_timings = _compute_word_timings_for_segment(
            seg_start=seg_start,
            seg_end=seg_end,
            seg_text=seg_text,
            seg_words=seg_words,
            clip_start=clip_start,
            clip_dur=clip_dur,
            timing_offset_sec=offset_sec,
        )
        if not word_timings:
            continue

        for grp in _group_words_pause_aware(word_timings, max_words=max_words):
            c_start = grp[0].start
            c_end = max(c_start + 0.08, grp[-1].end)
            chunks.append(
                ComposerCaptionChunk(
                    words=grp,
                    start=round(c_start, 3),
                    end=round(min(clip_dur, c_end), 3),
                    speaker=speaker or "SPEAKER_1",
                )
            )
    return chunks


def resolve_speaker_colors(
    config: ClipComposerConfig,
    speaker: str,
) -> tuple[str, str, str]:
    """Return `(primary_hex, highlight_hex, outline_hex)` for a speaker tag."""
    subs = config.subtitles
    if subs.multi_speaker_enabled and speaker != "SPEAKER_1" and speaker in subs.speaker_palettes:
        pal = subs.speaker_palettes[speaker]
        return pal.primary_hex, pal.highlight_hex, pal.outline_hex
    return subs.primary_color_hex, subs.highlight_color_hex, subs.outline_color_hex


def generate_composer_ass_file(
    output_ass_path: Path,
    config: ClipComposerConfig,
    segments: list[TranscriptSegment],
    clip_duration_sec: float,
    opening_duration_sec: float = 0.0,
    clip_start_sec: Optional[float] = None,
    clip_end_sec: Optional[float] = None,
) -> Optional[Path]:
    """Generate a 1:1 WYSIWYG .ass subtitle file driven by ClipComposerConfig."""
    subs = config.subtitles
    if not subs.enabled:
        return None

    play_res_x = max(320, int(config.canvas_width))
    play_res_y = max(320, int(config.canvas_height))
    intro_shift = max(0.0, float(opening_duration_sec))
    font_name = resolve_font_family(subs.custom_font_path, subs.font_family)

    primary_style_col = hex_to_ass_color(subs.primary_color_hex, trailing_amp=False)
    highlight_style_col = hex_to_ass_color(subs.highlight_color_hex, trailing_amp=False)
    outline_style_col = hex_to_ass_color(subs.outline_color_hex, trailing_amp=False)
    shadow_style_col = hex_to_ass_color(subs.shadow_color_hex, default_alpha=128, trailing_amp=False)

    footnote_col = hex_to_ass_color(subs.footnote.color_hex, trailing_amp=False)
    action_col_tag = hex_to_ass_color(subs.action_token_color_hex, trailing_amp=True)
    sec_col_tag = hex_to_ass_color(subs.secondary_keyword_color_hex, trailing_amp=True)
    pill_rgb_tag, pill_alpha_tag = _hex_to_ass_rgb_and_alpha(subs.active_word_bg_hex, default_alpha=0)

    chunks = build_composer_caption_chunks(
        config,
        segments,
        clip_start_sec=clip_start_sec,
        clip_end_sec=clip_end_sec,
    )
    dialogue_lines: list[str] = []

    pos_x = int(subs.pos_x)
    pos_y = int(subs.pos_y)
    blur_tag = f"\\blur{subs.glow_strength:.1f}" if subs.glow_strength > 0 else ""

    for chunk in chunks:
        p_hex, h_hex, o_hex = resolve_speaker_colors(config, chunk.speaker)
        p_tag = hex_to_ass_color(p_hex, trailing_amp=True)
        h_tag = hex_to_ass_color(h_hex, trailing_amp=True)
        o_tag = hex_to_ass_color(o_hex, trailing_amp=True)
        raw_words = [w.text for w in chunk.words]
        c_start = round(chunk.start + intro_shift, 3)
        c_end = min(clip_duration_sec, round(chunk.end + intro_shift, 3))
        if c_end <= c_start:
            continue

        chunk_layout = compute_caption_line_layout(
            config=config,
            raw_words=raw_words,
            active_idx=-1,
            canvas_w=play_res_x,
            canvas_h=play_res_y,
        )
        fit_pct = max(50, int(round(chunk_layout.scale_fit * 100.0)))

        if subs.caption_bg_mode == "full_line_box":
            box_path = _ass_rounded_rect_drawing(
                chunk_layout.box_w,
                chunk_layout.box_h,
                subs.active_word_bg_radius,
            )
            dialogue_lines.append(
                f"Dialogue: 1,{_format_ass_time(c_start)},{_format_ass_time(c_end)},"
                f"ComposerSub,,0,0,0,,{{\\an5\\pos({pos_x},{pos_y})\\bord0\\shad0"
                f"\\1c{pill_rgb_tag}\\1a{pill_alpha_tag}\\p1}}{box_path}{{\\p0}}"
            )

        if subs.auto_emoji_enabled and chunk_layout.emoji:
            em_y = int(round(chunk_layout.emoji_y))
            em_fs = max(24, int(round(subs.font_size * 0.95)))
            dialogue_lines.append(
                f"Dialogue: 2,{_format_ass_time(c_start)},{_format_ass_time(c_end)},"
                f"ComposerSub,,0,0,0,,{{\\an5\\pos({pos_x},{em_y})\\fnSegoe UI Emoji"
                f"\\fs{em_fs}\\bord2\\shad2}}{chunk_layout.emoji}"
            )

        if subs.style_mode == "plain":
            words_rendered = []
            for idx, w in enumerate(chunk.words):
                txt = w.text.upper() if subs.uppercase else w.text
                esc = _escape_ass_text(txt)
                if is_action_token(w.text):
                    words_rendered.append(f"{{\\c{action_col_tag}}}{esc}{{\\c{p_tag}}}")
                elif idx == chunk_layout.secondary_idx:
                    words_rendered.append(f"{{\\c{sec_col_tag}}}{esc}{{\\c{p_tag}}}")
                else:
                    words_rendered.append(esc)
            fit_tag = f"\\fscx{fit_pct}\\fscy{fit_pct}" if fit_pct < 100 else ""
            prefix = f"{{\\an5\\pos({pos_x},{pos_y})\\c{p_tag}\\3c{o_tag}{blur_tag}{fit_tag}}}"
            dialogue_lines.append(
                f"Dialogue: 2,{_format_ass_time(c_start)},{_format_ass_time(c_end)},"
                f"ComposerSub,,0,0,0,,{prefix}{' '.join(words_rendered)}"
            )
            continue

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

            word_layout = compute_caption_line_layout(
                config=config,
                raw_words=raw_words,
                active_idx=active_idx,
                canvas_w=play_res_x,
                canvas_h=play_res_y,
            )

            if subs.caption_bg_mode == "active_word_pill":
                pill_path = _ass_rounded_rect_drawing(
                    word_layout.pill_w,
                    word_layout.pill_h,
                    subs.active_word_bg_radius,
                )
                dialogue_lines.append(
                    f"Dialogue: 1,{_format_ass_time(ev_start)},{_format_ass_time(ev_end)},"
                    f"ComposerSub,,0,0,0,,{{\\an5\\pos({word_layout.pill_center_x:.1f},{pos_y})"
                    f"\\bord0\\shad0\\1c{pill_rgb_tag}\\1a{pill_alpha_tag}\\p1}}{pill_path}{{\\p0}}"
                )

            rendered_words: list[str] = []
            for j, w in enumerate(chunk.words):
                txt = w.text.upper() if subs.uppercase else w.text
                esc = _escape_ass_text(txt)
                action_word = is_action_token(w.text)
                if j == active_idx:
                    active_c = action_col_tag if action_word else h_tag
                    if subs.animation == "pop":
                        sc = max(55, int(round(112 * word_layout.scale_fit)))
                        rendered_words.append(
                            f"{{\\c{active_c}\\fscx{sc}\\fscy{sc}}}{esc}"
                            f"{{\\c{p_tag}\\fscx{fit_pct}\\fscy{fit_pct}}}"
                        )
                    elif subs.animation == "scale":
                        sc = max(55, int(round(116 * word_layout.scale_fit)))
                        rendered_words.append(
                            f"{{\\c{active_c}\\fscx{sc}\\fscy{sc}}}{esc}"
                            f"{{\\c{p_tag}\\fscx{fit_pct}\\fscy{fit_pct}}}"
                        )
                    else:
                        rendered_words.append(f"{{\\c{active_c}}}{esc}{{\\c{p_tag}}}")
                else:
                    if action_word:
                        rendered_words.append(f"{{\\c{action_col_tag}}}{esc}{{\\c{p_tag}}}")
                    elif j == word_layout.secondary_idx:
                        rendered_words.append(f"{{\\c{sec_col_tag}}}{esc}{{\\c{p_tag}}}")
                    else:
                        rendered_words.append(esc)

            anim_prefix = ""
            if active_idx == 0:
                if subs.animation == "pop":
                    s0 = max(45, int(round(90 * word_layout.scale_fit)))
                    anim_prefix = f"\\fscx{s0}\\fscy{s0}\\t(0,80,\\fscx{fit_pct}\\fscy{fit_pct})"
                elif subs.animation == "scale":
                    s0 = max(40, int(round(82 * word_layout.scale_fit)))
                    s1 = max(55, int(round(112 * word_layout.scale_fit)))
                    anim_prefix = (
                        f"\\fscx{s0}\\fscy{s0}\\t(0,95,\\fscx{s1}\\fscy{s1})"
                        f"\\t(95,180,\\fscx{fit_pct}\\fscy{fit_pct})"
                    )
            elif fit_pct < 100:
                anim_prefix = f"\\fscx{fit_pct}\\fscy{fit_pct}"

            base_tag = f"{{\\an5\\pos({pos_x},{pos_y})\\c{p_tag}\\3c{o_tag}{blur_tag}{anim_prefix}}}"
            line_text = base_tag + " ".join(rendered_words)
            dialogue_lines.append(
                f"Dialogue: 2,{_format_ass_time(ev_start)},{_format_ass_time(ev_end)},"
                f"ComposerSub,,0,0,0,,{line_text}"
            )

    if subs.footnote.enabled and subs.footnote.text.strip():
        fn_y = min(play_res_y - 36, pos_y + int(subs.footnote.offset_y))
        fn_text = _escape_ass_text(subs.footnote.text.strip())
        fn_start = min(clip_duration_sec, intro_shift)
        if clip_duration_sec > fn_start:
            dialogue_lines.append(
                f"Dialogue: 1,{_format_ass_time(fn_start)},{_format_ass_time(clip_duration_sec)},"
                f"FootnoteSub,,0,0,0,,{{\\an5\\pos({pos_x},{fn_y})}}{fn_text}"
            )

    if not dialogue_lines:
        return None

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
            f"Style: ComposerSub,{font_name},{int(subs.font_size)},{primary_style_col},{highlight_style_col},"
            f"{outline_style_col},{shadow_style_col},-1,0,0,0,100,100,0,0,1,{subs.outline_width:.1f},"
            f"{subs.shadow_depth:.1f},5,40,40,40,1",
            f"Style: FootnoteSub,{font_name},{int(subs.footnote.font_size)},{footnote_col},{footnote_col},"
            f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.5,1.0,5,40,40,20,1",
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
