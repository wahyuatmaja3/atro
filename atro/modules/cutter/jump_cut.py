"""Jump-cut planner: drop long pauses and filler words based on word timestamps.

Pure functions (no FFmpeg) plus an FFmpeg command builder for the trimmed intermediate clip.
All times of `plan_keep_ranges` are absolute source seconds; `remap_time` maps them to the trimmed timeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from atro.core.schemas import TranscriptSegment, TranscriptWord

FILLER_WORDS: frozenset[str] = frozenset(
    {"eee", "ee", "eh", "ehm", "em", "emm", "hmm", "hm", "mm", "umm", "um", "uh", "uhh", "anu", "ehh", "aaa", "hmmm"}
)
LEAD_SEC = 0.06
TAIL_SEC = 0.10
MIN_RANGE_SEC = 0.15
MIN_SAVED_SEC = 0.4
_PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True)
class KeepRange:
    src_start: float
    src_end: float
    new_start: float

    @property
    def length(self) -> float:
        return self.src_end - self.src_start


def is_filler(word: str) -> bool:
    return _PUNCT_RE.sub("", word.lower()) in FILLER_WORDS


def collect_words(segments: Sequence[TranscriptSegment], start: float, end: float) -> list[TranscriptWord]:
    words: list[TranscriptWord] = []
    for seg in segments:
        if seg.end < start or seg.start > end:
            continue
        for w in seg.words:
            if w.end > start and w.start < end:
                words.append(w)
    words.sort(key=lambda w: w.start)
    return words


def plan_keep_ranges(
    words: Sequence[TranscriptWord],
    window_start: float,
    window_end: float,
    max_gap_sec: float = 0.35,
    remove_fillers: bool = True,
) -> list[KeepRange]:
    """Return ranges (absolute source time) to keep, or `[]` when trimming is pointless."""
    if not words or window_end <= window_start:
        return []

    speech = [w for w in words if not (remove_fillers and is_filler(w.word))]
    fillers = [w for w in words if remove_fillers and is_filler(w.word)]
    if not speech:
        return []

    raw: list[list[float]] = []
    prev_end = 0.0
    for w in speech:
        s = max(window_start, w.start - LEAD_SEC)
        e = min(window_end, w.end + TAIL_SEC)
        if raw and (s <= raw[-1][1] or (w.start - prev_end) <= max_gap_sec):
            raw[-1][1] = max(raw[-1][1], e)
        else:
            raw.append([s, e])
        prev_end = max(prev_end, w.end)

    # Carve filler spans out of kept ranges
    for f in fillers:
        fs, fe = f.start + 0.01, f.end - 0.01
        if fe <= fs:
            continue
        carved: list[list[float]] = []
        for s, e in raw:
            if fe <= s or fs >= e:
                carved.append([s, e])
                continue
            if fs - s >= MIN_RANGE_SEC:
                carved.append([s, fs])
            if e - fe >= MIN_RANGE_SEC:
                carved.append([fe, e])
        raw = carved

    kept = [r for r in raw if r[1] - r[0] >= MIN_RANGE_SEC]
    if not kept:
        return []

    total_src = window_end - window_start
    total_kept = sum(r[1] - r[0] for r in kept)
    if total_src - total_kept < MIN_SAVED_SEC:
        return []

    out: list[KeepRange] = []
    cursor = 0.0
    for s, e in kept:
        out.append(KeepRange(round(s, 3), round(e, 3), round(cursor, 3)))
        cursor += e - s
    return out


def trimmed_duration(ranges: Sequence[KeepRange]) -> float:
    return round(sum(r.length for r in ranges), 3) if ranges else 0.0


def remap_time(t: float, ranges: Sequence[KeepRange]) -> Optional[float]:
    """Map absolute source time to trimmed timeline; `None` when `t` falls inside a cut."""
    for r in ranges:
        if r.src_start <= t <= r.src_end:
            return round(r.new_start + (t - r.src_start), 3)
    return None


def _remap_span(start: float, end: float, ranges: Sequence[KeepRange]) -> Optional[tuple[float, float]]:
    pieces = [
        (max(start, r.src_start), min(end, r.src_end), r)
        for r in ranges
        if min(end, r.src_end) > max(start, r.src_start)
    ]
    if not pieces:
        return None
    first, last = pieces[0], pieces[-1]
    ns = first[2].new_start + (first[0] - first[2].src_start)
    ne = last[2].new_start + (last[1] - last[2].src_start)
    return round(ns, 3), round(max(ne, ns + 0.05), 3)


def remap_words(words: Sequence[TranscriptWord], ranges: Sequence[KeepRange]) -> list[TranscriptWord]:
    out: list[TranscriptWord] = []
    for w in words:
        span = _remap_span(w.start, w.end, ranges)
        if span is not None:
            out.append(TranscriptWord(word=w.word, start=span[0], end=span[1]))
    return out


def remap_segments(
    segments: Sequence[TranscriptSegment],
    ranges: Sequence[KeepRange],
    remove_fillers: bool = True,
) -> list[TranscriptSegment]:
    """Remap transcript segments (absolute source time) onto the trimmed clip timeline (starts at 0)."""
    out: list[TranscriptSegment] = []
    for seg in segments:
        words = [w for w in seg.words if not (remove_fillers and is_filler(w.word))]
        new_words = remap_words(words, ranges)
        if new_words:
            ns, ne = new_words[0].start, new_words[-1].end
            text = " ".join(w.word.strip() for w in new_words).strip()
        else:
            span = _remap_span(seg.start, seg.end, ranges)
            if span is None:
                continue
            ns, ne = span
            text = seg.text
        out.append(
            TranscriptSegment(index=len(out), start=ns, end=max(ne, ns + 0.05), text=text, words=new_words)
        )
    return out


def build_trim_ffmpeg_command(
    ffmpeg_path: Path,
    source_path: Path,
    output_path: Path,
    window_start: float,
    window_end: float,
    ranges: Sequence[KeepRange],
    filter_script_path: Path,
    has_audio: bool,
    fps: float,
) -> tuple[list[str], str]:
    """Return `(command, filter_script_text)` that renders the concatenated keep-ranges to `output_path`."""
    n = len(ranges)
    lines: list[str] = []
    concat_inputs: list[str] = []
    for i, r in enumerate(ranges):
        a = round(r.src_start - window_start, 3)
        b = round(r.src_end - window_start, 3)
        lines.append(f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            lines.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]")
            concat_inputs.append(f"[v{i}][a{i}]")
        else:
            concat_inputs.append(f"[v{i}]")
    if has_audio:
        lines.append("".join(concat_inputs) + f"concat=n={n}:v=1:a=1[vc][ac]")
    else:
        lines.append("".join(concat_inputs) + f"concat=n={n}:v=1:a=0[vc]")
    lines.append(f"[vc]fps={max(1.0, round(fps, 3))}[vout]")
    script = ";\n".join(lines)

    cmd = [
        str(ffmpeg_path),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{window_start:.3f}",
        "-t",
        f"{window_end - window_start:.3f}",
        "-i",
        str(source_path),
        "-filter_complex_script",
        str(filter_script_path),
        "-map",
        "[vout]",
    ]
    if has_audio:
        cmd += ["-map", "[ac]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", str(output_path)]
    return cmd, script
