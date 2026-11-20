"""Viral short-form video overlay filters (color pop, fade, progress bar, ASS burn-in)."""

from __future__ import annotations

from typing import Optional

from atro.core.schemas import EditFormat


def _escape_filter_filename(filename: str) -> str:
    return filename.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_viral_video_overlay_filters(
    in_label: str,
    out_label: str,
    width: int,
    height: int,
    duration_sec: float,
    ass_filename: Optional[str],
    enable_viral_polish: bool,
    edit_format: EditFormat = "podcast",
) -> list[str]:
    """Build ordered FFmpeg filter_complex lines turning `[in_label]` into `[out_label]`."""
    dur = max(0.5, round(float(duration_sec), 3))
    lines: list[str] = []
    cur = in_label

    if enable_viral_polish:
        fade_out_start = max(0.0, round(dur - 0.35, 3))
        if edit_format == "game":
            eq_filter = "eq=contrast=1.08:saturation=1.18:brightness=0.01"
            bar_color = "#38BDF8"
        else:
            eq_filter = "eq=contrast=1.05:saturation=1.10:brightness=0.01"
            bar_color = "#FACC15"

        lines.append(
            f"[{cur}]{eq_filter},"
            f"fade=t=in:st=0:d=0.25,fade=t=out:st={fade_out_start:.3f}:d=0.35[v_polish]"
        )
        lines.append(f"color=c={bar_color}:s={width}x8:d={dur:.3f}[pbar]")
        lines.append(
            f"[v_polish][pbar]overlay=x='-w+w*min(1,t/{dur:.3f})':y=H-8:shortest=1[v_pbar]"
        )
        cur = "v_pbar"

    if ass_filename:
        safe_name = _escape_filter_filename(ass_filename)
        lines.append(f"[{cur}]ass=filename={safe_name}[v_sub]")
        cur = "v_sub"

    lines.append(f"[{cur}]format=yuv420p[{out_label}]")
    return lines
