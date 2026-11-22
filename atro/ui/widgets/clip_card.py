"""Flat ClipCard widget displaying a rendered clip's score, reason, and playback actions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
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

    customize_requested = pyqtSignal(int)
    selection_changed = pyqtSignal(int, bool)
    publish_kit_requested = pyqtSignal(int)

    def __init__(self, clip: RenderedClip, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._clip = clip
        self.setObjectName("ClipCard")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # Header row: batch checkbox + index + title + virality badge
        header = QHBoxLayout()
        header.setSpacing(8)

        self.select_chk = QCheckBox()
        self.select_chk.setChecked(True)
        self.select_chk.setToolTip("Pilih klip ini untuk edit / render ulang massal")
        self.select_chk.toggled.connect(
            lambda checked: self.selection_changed.emit(self._clip.clip_index, checked)
        )

        title_lbl = QLabel(f"#{clip.clip_index:02d}  {clip.title}")
        title_lbl.setStyleSheet("font-size: 14px; font-weight: 600;")
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

        header.addWidget(self.select_chk, 0)
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
        if clip.edit_format:
            fmt_labels = {
                "game": "Game",
                "gaming_split": "Gaming Split",
                "twitch_comment": "Twitch Comment",
                "subathon": "Subathon",
                "podcast": "Podcast",
            }
            edit_tags.append(f"Format: {fmt_labels.get(clip.edit_format, clip.edit_format)}")
        if clip.opening_tts_used:
            edit_tags.append("Suara Opening: Ya")
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
        reason_lbl.setObjectName("MutedText")
        root.addWidget(reason_lbl)

        # Actions row
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()

        publish_btn = QPushButton("Publish Kit (.zip)")
        publish_btn.setObjectName("SmallAction")
        publish_btn.setToolTip("Ekspor MP4 + Cover PNG 1080x1920 + Caption/Hashtag siap upload")
        publish_btn.clicked.connect(lambda: self.publish_kit_requested.emit(self._clip.clip_index))

        locate_btn = QPushButton("Lokasi File")
        locate_btn.setObjectName("SmallAction")
        locate_btn.clicked.connect(self._reveal_in_explorer)

        customize_btn = QPushButton("Ubah Style & Live Preview ->")
        customize_btn.setObjectName("SmallAction")
        customize_btn.clicked.connect(lambda: self.customize_requested.emit(self._clip.clip_index))

        play_btn = QPushButton("Putar Klip")
        play_btn.setObjectName("PrimaryButton")
        play_btn.clicked.connect(self._open_clip)

        actions.addWidget(customize_btn)
        actions.addWidget(publish_btn)
        actions.addWidget(locate_btn)
        actions.addWidget(play_btn)
        root.addLayout(actions)

    def is_selected(self) -> bool:
        return self.select_chk.isChecked()

    def set_selected(self, checked: bool) -> None:
        self.select_chk.setChecked(checked)

    def clip_index(self) -> int:
        return self._clip.clip_index

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
