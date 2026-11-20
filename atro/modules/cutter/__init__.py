"""Module 4: Accurate video cutting, smart face-tracking framing, subtitles, backsound, and viral edits."""

from atro.modules.cutter.service import (
    CutterService,
    build_ffmpeg_cut_command,
    build_ffmpeg_viral_cut_command,
    compute_padded_window,
)

__all__ = [
    "CutterService",
    "build_ffmpeg_cut_command",
    "build_ffmpeg_viral_cut_command",
    "compute_padded_window",
]
