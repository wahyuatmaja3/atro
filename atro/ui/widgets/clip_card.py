"""Flat ClipCard widget displaying a rendered clip's score, reason, and playback actions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from atro.core.schemas import RenderedClip


def _fmt_sec(sec: float) -> str:
    total = int(round(sec))
    mins, rem = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{rem:02d}"
    return f"{mins:02d}:{rem:02d}"


class ClipCard(QFrame):
    """Displays metadata and playback actions for one rendered MP4 clip."""

    def __init__(self, clip: RenderedClip, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._clip = clip
        self.setObjectName("ClipCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # Header row: index + title + virality badge
        header = QHBoxLayout()
        header.setSpacing(8)

        title_lbl = QLabel(f"#{clip.clip_index:02d}  {clip.title}")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #F8FAFC;")
        title_lbl.setWordWrap(True)

        score_pill = QLabel(f"Skor {clip.virality_score}/10")
        score_pill.setObjectName("StatusPill")
        if clip.virality_score >= 8:
            tone = "ok"
        elif clip.virality_score >= 6:
            tone = "info"
        else:
            tone = "warn"
        score_pill.setProperty("tone", tone)

        header.addWidget(title_lbl, 1)
        header.addWidget(score_pill, 0)
        root.addLayout(header)

        # Timing + File size row
        size_mb = clip.file_size_bytes / (1024 * 1024)
        timing_lbl = QLabel(
            f"Waktu: {_fmt_sec(clip.padded_start_sec)} – {_fmt_sec(clip.padded_end_sec)}  •  "
            f"Durasi: {clip.output_duration_sec:.1f}s  •  Ukuran: {size_mb:.2f} MB"
        )
        timing_lbl.setObjectName("MutedText")
        root.addWidget(timing_lbl)

        edit_tags: list[str] = []
        if clip.subtitles_burned:
            edit_tags.append("Subtitle: Ya")
        if clip.backsound_used:
            edit_tags.append(f"Backsound: {clip.backsound_used}")
        if clip.framing_summary:
            edit_tags.append(f"Framing: {clip.framing_summary}")
        if edit_tags:
            tags_lbl = QLabel("  •  ".join(edit_tags))
            tags_lbl.setObjectName("MutedText")
            tags_lbl.setWordWrap(True)
            root.addWidget(tags_lbl)

        # Reason text
        reason_lbl = QLabel(clip.reason)
        reason_lbl.setWordWrap(True)
        reason_lbl.setStyleSheet("color: #CBD5E1; font-size: 12px;")
        root.addWidget(reason_lbl)

        # Actions row
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()

        locate_btn = QPushButton("Lokasi File")
        locate_btn.setObjectName("SmallAction")
        locate_btn.clicked.connect(self._reveal_in_explorer)

        play_btn = QPushButton("Putar Klip")
        play_btn.setObjectName("PrimaryButton")
        play_btn.clicked.connect(self._open_clip)

        actions.addWidget(locate_btn)
        actions.addWidget(play_btn)
        root.addLayout(actions)

    def _open_clip(self) -> None:
        path = Path(self._clip.output_path)
        if path.is_file():
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])

    def _reveal_in_explorer(self) -> None:
        path = Path(self._clip.output_path)
        if os.name == "nt" and path.exists():
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif path.parent.exists() and os.name == "nt":
            os.startfile(str(path.parent))  # type: ignore[attr-defined]
