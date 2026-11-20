"""WebVTT / SRT parser, YouTube rolling-line deduplicator, and quality validator."""

from __future__ import annotations

import html
import re
from typing import Iterable

from atro.core.schemas import TranscriptSegment

_TS_PATTERN = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_SPEECH_RE = re.compile(r"^\s*(?:[\[(][^\]\)]+[\])]|♪+|♫+|[-–—\.]+)\s*$")


def _parse_timestamp_sec(ts: str) -> float:
    normalized = ts.strip().replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 3:
        hours, mins, secs = int(parts[0]), int(parts[1]), float(parts[2])
    elif len(parts) == 2:
        hours, mins, secs = 0, int(parts[0]), float(parts[1])
    else:
        raise ValueError(f"Format timestamp tidak valid: {ts}")
    return round(hours * 3600.0 + mins * 60.0 + secs, 3)


def _clean_cue_lines(raw_lines: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in raw_lines:
        stripped = _TAG_RE.sub("", raw)
        unescaped = html.unescape(stripped)
        text = _WHITESPACE_RE.sub(" ", unescaped).strip()
        if text:
            cleaned.append(text)
    return cleaned


def _deduplicate_cues(raw_cues: list[tuple[float, float, list[str]]]) -> list[TranscriptSegment]:
    """Convert raw cues into clean, non-rolling TranscriptSegments."""
    segments: list[TranscriptSegment] = []
    prev_full_text = ""

    for start, end, lines in raw_cues:
        if end <= start:
            continue
        duration = end - start
        if not lines:
            continue

        joined = " ".join(lines).strip()
        if not joined:
            continue

        # Skip ultra-short YouTube flash transition cues (< 50ms) when text duplicates previous
        if duration < 0.05 and joined == prev_full_text:
            continue

        # Deduplicate rolling captions where line 0 repeats the previous cue
        effective_lines = list(lines)
        if len(effective_lines) >= 2 and prev_full_text:
            first_line = effective_lines[0]
            if first_line == prev_full_text or prev_full_text.endswith(first_line):
                effective_lines = effective_lines[1:]

        text = " ".join(effective_lines).strip()
        if not text:
            prev_full_text = joined
            continue

        # Skip exact repeat right after the previous cue
        if segments and text == segments[-1].text:
            # Extend previous cue end slightly if contiguous
            if start <= segments[-1].end + 0.5:
                segments[-1] = TranscriptSegment(
                    index=segments[-1].index,
                    start=segments[-1].start,
                    end=max(segments[-1].end, end),
                    text=segments[-1].text,
                )
            prev_full_text = joined
            continue

        # Strip overlapping prefix if text starts with previous segment text
        if segments and len(segments[-1].text) >= 6 and text.startswith(segments[-1].text + " "):
            remainder = text[len(segments[-1].text) :].strip()
            if remainder:
                text = remainder

        segments.append(
            TranscriptSegment(
                index=len(segments),
                start=round(start, 3),
                end=round(end, 3),
                text=text,
            )
        )
        prev_full_text = joined

    return segments


def parse_vtt(content: str) -> list[TranscriptSegment]:
    """Parse WebVTT subtitle content into deduplicated TranscriptSegments."""
    text = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n{2,}", text)
    raw_cues: list[tuple[float, float, list[str]]] = []

    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        header = lines[0].strip().upper()
        if header.startswith("WEBVTT") or header.startswith("NOTE") or header.startswith("STYLE") or header.startswith("REGION"):
            continue

        ts_idx = -1
        ts_match = None
        for idx, line in enumerate(lines):
            m = _TS_PATTERN.search(line)
            if m:
                ts_idx = idx
                ts_match = m
                break

        if ts_match is None:
            continue

        start = _parse_timestamp_sec(ts_match.group("start"))
        end = _parse_timestamp_sec(ts_match.group("end"))
        cue_lines = _clean_cue_lines(lines[ts_idx + 1 :])
        if cue_lines and end > start:
            raw_cues.append((start, end, cue_lines))

    return _deduplicate_cues(raw_cues)


def parse_srt(content: str) -> list[TranscriptSegment]:
    """Parse SRT subtitle content into deduplicated TranscriptSegments."""
    text = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n{2,}", text)
    raw_cues: list[tuple[float, float, list[str]]] = []

    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue

        ts_idx = -1
        ts_match = None
        for idx, line in enumerate(lines):
            m = _TS_PATTERN.search(line)
            if m:
                ts_idx = idx
                ts_match = m
                break

        if ts_match is None:
            continue

        start = _parse_timestamp_sec(ts_match.group("start"))
        end = _parse_timestamp_sec(ts_match.group("end"))
        cue_lines = _clean_cue_lines(lines[ts_idx + 1 :])
        if cue_lines and end > start:
            raw_cues.append((start, end, cue_lines))

    return _deduplicate_cues(raw_cues)


def parse_subtitle_file(path_or_content: str, ext: str) -> list[TranscriptSegment]:
    """Dispatch subtitle parsing by extension."""
    ext_low = ext.lower().lstrip(".")
    if ext_low == "vtt":
        return parse_vtt(path_or_content)
    if ext_low == "srt":
        return parse_srt(path_or_content)
    raise ValueError(f"Ekstensi subtitle belum didukung parser: .{ext_low}")


def evaluate_subtitle_quality(
    segments: list[TranscriptSegment],
    video_duration_sec: float,
    is_auto_generated: bool,
) -> tuple[bool, str]:
    """Validate whether parsed subtitle segments are complete and accurate enough for clipping."""
    if not segments:
        return False, "Subtitle kosong atau tidak memiliki cue valid."

    for seg in segments:
        if seg.start < 0 or seg.end <= seg.start:
            return False, f"Timestamp subtitle tidak valid pada segmen {seg.index}."

    total_words = sum(len(seg.text.split()) for seg in segments)
    if video_duration_sec >= 30.0:
        if len(segments) < 3 or total_words < 15:
            return (
                False,
                f"Terlalu sedikit teks ({len(segments)} segmen, {total_words} kata) untuk video {video_duration_sec:.1f}s.",
            )
    elif total_words < 3:
        return False, f"Kata terlalu sedikit ({total_words} kata)."

    non_speech_count = sum(1 for seg in segments if _NON_SPEECH_RE.match(seg.text))
    non_speech_ratio = non_speech_count / len(segments)
    if non_speech_ratio >= 0.50:
        return (
            False,
            f"Terlalu banyak tag non-speech ({non_speech_ratio * 100:.0f}% segmen berisi [Musik]/efek).",
        )

    span = segments[-1].end - segments[0].start
    coverage_ratio = span / max(video_duration_sec, 1.0)
    if video_duration_sec >= 60.0 and coverage_ratio < 0.35:
        return (
            False,
            f"Cakupan waktu subtitle terlalu rendah ({coverage_ratio * 100:.1f}% dari durasi video).",
        )

    if is_auto_generated and len(segments) > 2:
        extreme_overlaps = 0
        for prev, curr in zip(segments, segments[1:]):
            if curr.start < prev.end - 0.3:
                extreme_overlaps += 1
        overlap_ratio = extreme_overlaps / (len(segments) - 1)
        if overlap_ratio >= 0.30:
            return (
                False,
                f"Auto-subtitle memiliki overlap timestamp ekstrem ({overlap_ratio * 100:.0f}%).",
            )

    notes = (
        f"Subtitle {'auto' if is_auto_generated else 'manual'} valid "
        f"({len(segments)} segmen, {total_words} kata, cakupan {min(100.0, coverage_ratio * 100):.1f}%)."
    )
    return True, notes
