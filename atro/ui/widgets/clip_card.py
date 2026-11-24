"""OpusClip-style clip feed card with 9:16 hover-play thumbnail, virality badge, and Export menu."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from atro.core.schemas import RenderedClip
from atro.ui.components import create_vector_icon

_CLIP_THUMB_CACHE: dict[str, QPixmap] = {}


def _fmt_sec(sec: float) -> str:
    total = max(0, int(round(sec)))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def _load_video_thumb(video_path: str) -> Optional[QPixmap]:
    if not video_path:
        return None
    cached = _CLIP_THUMB_CACHE.get(video_path)
    if cached is not None:
        return cached
    p = Path(video_path)
    if not p.is_file():
        return None
    try:
        import cv2
        from PyQt6.QtGui import QImage

        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            cap.release()
            return None
        total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if total and total > 12:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(min(total * 0.12, 45)))
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return None
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg)
        if len(_CLIP_THUMB_CACHE) > 80:
            _CLIP_THUMB_CACHE.clear()
        _CLIP_THUMB_CACHE[video_path] = pix
        return pix
    except Exception:
        return None


class _ClipMiniThumb(QPushButton):
    """Compact 9:16 video thumbnail preview with hover quick-play button."""

    def __init__(self, video_path: str, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._video_path = video_path
        self._title = title
        self._hovered = False
        self.setFixedSize(82, 146)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Klik untuk memutar klip MP4")
        self.setFlat(True)
        self._thumb = _load_video_thumb(video_path)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)

        clip_path = QPainterPath()
        clip_path.addRoundedRect(rect, 9.0, 9.0)
        p.setClipPath(clip_path)

        if self._thumb is not None and not self._thumb.isNull():
            scaled = self._thumb.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            sx = max(0, (scaled.width() - self.width()) // 2)
            sy = max(0, (scaled.height() - self.height()) // 2)
            p.drawPixmap(0, 0, scaled, sx, sy, self.width(), self.height())
            if self._hovered:
                p.fillRect(rect, QColor(9, 10, 15, 110))
        else:
            grad = QLinearGradient(0, 0, self.width(), self.height())
            grad.setColorAt(0.0, QColor("#1F183D"))
            grad.setColorAt(0.55, QColor("#121728"))
            grad.setColorAt(1.0, QColor("#0B2530"))
            p.fillRect(rect, grad)
            p.setPen(QColor("#9CA3AF"))
            p.drawText(
                QRectF(6, 12, self.width() - 12, 34),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "9:16",
            )

        # Hover or resting Play badge
        cx, cy = self.width() / 2.0, self.height() / 2.0
        radius = 18.0 if self._hovered else 14.5
        circle_bg = QColor("#7C3AED") if self._hovered else QColor(18, 20, 28, 195)
        p.setPen(QPen(QColor("#FFFFFF" if self._hovered else "#06B6D4"), 1.5))
        p.setBrush(circle_bg)
        p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        tri = QPainterPath()
        off = 5.5 if self._hovered else 4.5
        tri.moveTo(cx - off * 0.55, cy - off)
        tri.lineTo(cx + off * 1.15, cy)
        tri.lineTo(cx - off * 0.55, cy + off)
        tri.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawPath(tri)

        p.setClipping(False)
        border_col = QColor("#7C3AED" if self._hovered else "#2C3245")
        p.setPen(QPen(border_col, 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 9.0, 9.0)
        p.end()


class ClipCard(QFrame):
    """OpusClip-style clip feed card: 9:16 preview left, virality + summary center, Studio/Export right."""

    customize_requested = pyqtSignal(int)
    selection_changed = pyqtSignal(int, bool)
    publish_kit_requested = pyqtSignal(int)

    def __init__(self, clip: RenderedClip, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._clip = clip
        self.setObjectName("ClipCard")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(14)

        # Left column: Batch check + 9:16 mini thumbnail preview
        left_col = QHBoxLayout()
        left_col.setSpacing(10)
        left_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.select_chk = QCheckBox()
        self.select_chk.setChecked(True)
        self.select_chk.setToolTip("Pilih klip ini untuk edit / render ulang massal")
        self.select_chk.toggled.connect(
            lambda checked: self.selection_changed.emit(self._clip.clip_index, checked)
        )
        left_col.addWidget(self.select_chk, 0, Qt.AlignmentFlag.AlignTop)

        self.thumb_btn = _ClipMiniThumb(clip.output_path, clip.title, parent=self)
        self.thumb_btn.clicked.connect(self._open_clip)
        left_col.addWidget(self.thumb_btn)
        outer.addLayout(left_col, 0)

        # Middle column: Virality score badge + Title + Metadata + Hook summary
        mid_col = QVBoxLayout()
        mid_col.setContentsMargins(0, 2, 0, 2)
        mid_col.setSpacing(6)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)

        score_100 = max(1, min(100, int(round(clip.virality_score * 10))))
        self.score_pill = QLabel(f"{score_100}/100")
        self.score_pill.setObjectName("ViralityBadge")
        if score_100 >= 80:
            tier = "high"
        elif score_100 >= 60:
            tier = "mid"
        else:
            tier = "low"
        self.score_pill.setProperty("tier", tier)
        self.score_pill.setToolTip(f"Virality Score ({clip.virality_score:.1f}/10)")

        dur_pill = QLabel(f"{clip.output_duration_sec:.0f}s")
        dur_pill.setObjectName("StatusPill")
        dur_pill.setProperty("tone", "cyan")

        idx_pill = QLabel(f"#{clip.clip_index:02d}")
        idx_pill.setObjectName("StatusPill")

        badge_row.addWidget(self.score_pill)
        badge_row.addWidget(dur_pill)
        badge_row.addWidget(idx_pill)
        badge_row.addStretch(1)
        mid_col.addLayout(badge_row)

        title_lbl = QLabel(clip.title)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #F3F4F6;")
        title_lbl.setWordWrap(True)
        mid_col.addWidget(title_lbl)

        size_mb = clip.file_size_bytes / (1024 * 1024)
        timing_lbl = QLabel(
            f"{_fmt_sec(clip.padded_start_sec)} – {_fmt_sec(clip.padded_end_sec)}  •  "
            f"Durasi {clip.output_duration_sec:.1f}s  •  {size_mb:.2f} MB"
        )
        timing_lbl.setObjectName("MutedText")
        mid_col.addWidget(timing_lbl)

        edit_tags: list[str] = []
        if clip.edit_format:
            fmt_labels = {
                "game": "Game",
                "gaming_split": "Gaming Split",
                "twitch_comment": "Twitch Comment",
                "subathon": "Subathon",
                "podcast": "Podcast",
            }
            edit_tags.append(fmt_labels.get(clip.edit_format, clip.edit_format))
        if clip.opening_tts_used:
            edit_tags.append("Opening TTS")
        if clip.subtitles_burned:
            edit_tags.append("Subtitle")
        if clip.backsound_used:
            edit_tags.append(f"BGM: {clip.backsound_used}")
        if clip.framing_summary:
            edit_tags.append(clip.framing_summary)
        if edit_tags:
            tags_lbl = QLabel("  •  ".join(edit_tags))
            tags_lbl.setStyleSheet("font-size: 11px; color: #06B6D4; font-weight: 600;")
            tags_lbl.setWordWrap(True)
            mid_col.addWidget(tags_lbl)

        if clip.reason:
            reason_lbl = QLabel(clip.reason)
            reason_lbl.setWordWrap(True)
            reason_lbl.setObjectName("MutedText")
            mid_col.addWidget(reason_lbl)

        mid_col.addStretch(1)
        outer.addLayout(mid_col, 1)

        # Right column: Secondary "Edit di Studio" + Primary "Export"
        right_col = QVBoxLayout()
        right_col.setContentsMargins(6, 4, 0, 4)
        right_col.setSpacing(8)
        right_col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.customize_btn = QPushButton("Edit di Studio")
        self.customize_btn.setObjectName("SmallAction")
        self.customize_btn.setIcon(create_vector_icon("editor", "#F3F4F6", 15))
        self.customize_btn.setIconSize(QSize(15, 15))
        self.customize_btn.setMinimumWidth(148)
        self.customize_btn.setToolTip("Buka klip ini di 3-Panel Editor Studio")
        self.customize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.customize_btn.clicked.connect(
            lambda: self.customize_requested.emit(self._clip.clip_index)
        )

        self.export_btn = QPushButton("Export ▾")
        self.export_btn.setObjectName("PrimaryButton")
        self.export_btn.setIcon(create_vector_icon("download", "#FFFFFF", 15))
        self.export_btn.setIconSize(QSize(15, 15))
        self.export_btn.setMinimumWidth(148)
        self.export_btn.setToolTip("Export Publish Kit (.zip), putar MP4, atau buka folder file")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        export_menu = QMenu(self.export_btn)
        act_kit = export_menu.addAction("Export Publish Kit (.zip)")
        act_kit.triggered.connect(lambda: self.publish_kit_requested.emit(self._clip.clip_index))
        act_play = export_menu.addAction("Putar Video (MP4)")
        act_play.triggered.connect(self._open_clip)
        act_loc = export_menu.addAction("Buka Lokasi File")
        act_loc.triggered.connect(self._reveal_in_explorer)
        self.export_btn.setMenu(export_menu)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        play_mini = QPushButton("Putar")
        play_mini.setObjectName("GhostButton")
        play_mini.setIcon(create_vector_icon("play", "#9CA3AF", 13))
        play_mini.setToolTip("Putar file hasil render MP4")
        play_mini.clicked.connect(self._open_clip)

        loc_mini = QPushButton("Folder")
        loc_mini.setObjectName("GhostButton")
        loc_mini.setIcon(create_vector_icon("folder", "#9CA3AF", 13))
        loc_mini.setToolTip("Tampilkan file di Explorer")
        loc_mini.clicked.connect(self._reveal_in_explorer)
        quick_row.addWidget(play_mini)
        quick_row.addWidget(loc_mini)

        right_col.addWidget(self.customize_btn)
        right_col.addWidget(self.export_btn)
        right_col.addLayout(quick_row)
        outer.addLayout(right_col, 0)

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
