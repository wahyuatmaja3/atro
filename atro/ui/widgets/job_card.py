"""Compact flat JobCard widget showing job status, progress, and quick actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from atro.core.schemas import JobState, JobStatus

_STATUS_TONE = {
    JobStatus.QUEUED: "neutral",
    JobStatus.DOWNLOADING: "info",
    JobStatus.TRANSCRIBING: "info",
    JobStatus.ANALYZING: "info",
    JobStatus.CUTTING: "info",
    JobStatus.COMPLETED: "ok",
    JobStatus.FAILED: "error",
    JobStatus.CANCELLED: "warn",
}

_ACTIVE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.TRANSCRIBING,
    JobStatus.ANALYZING,
    JobStatus.CUTTING,
}


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def _resolve_display_title(state: JobState) -> str:
    if state.artifacts.metadata_json:
        meta_path = Path(state.artifacts.metadata_json)
        if meta_path.is_file():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                title = str(data.get("title") or "").strip()
                if title:
                    return title
            except Exception:
                pass
    return state.request.display_source


class JobCard(QFrame):
    """Flat card representing one AutoClip job in the queue/history list."""

    clicked = pyqtSignal(str)
    open_folder_requested = pyqtSignal(str)
    resume_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal(str)

    def __init__(self, state: JobState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.job_id = state.job_id
        self.setObjectName("JobCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # Header: title + status pill
        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.title_label = QLabel()
        self.title_label.setWordWrap(False)
        self.title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.status_pill = QLabel()
        self.status_pill.setObjectName("StatusPill")
        top_row.addWidget(self.title_label, 1)
        top_row.addWidget(self.status_pill, 0)
        root.addLayout(top_row)

        # Metadata row
        self.meta_label = QLabel()
        self.meta_label.setObjectName("MutedText")
        root.addWidget(self.meta_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        root.addWidget(self.progress_bar)

        # Bottom row: stage info + action buttons
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)
        self.stage_label = QLabel()
        self.stage_label.setObjectName("MutedText")
        bottom_row.addWidget(self.stage_label, 1)

        self.folder_btn = QPushButton("Folder")
        self.folder_btn.setObjectName("SmallAction")
        self.folder_btn.clicked.connect(lambda: self.open_folder_requested.emit(self.job_id))

        self.resume_btn = QPushButton("Resume")
        self.resume_btn.setObjectName("SmallAction")
        self.resume_btn.clicked.connect(lambda: self.resume_requested.emit(self.job_id))

        self.cancel_btn = QPushButton("Batal")
        self.cancel_btn.setObjectName("SmallAction")
        self.cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(self.job_id))

        bottom_row.addWidget(self.folder_btn)
        bottom_row.addWidget(self.resume_btn)
        bottom_row.addWidget(self.cancel_btn)
        root.addLayout(bottom_row)

        self.update_state(state)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        _repolish(self)

    def update_state(self, state: JobState) -> None:
        display_title = _resolve_display_title(state)
        if len(display_title) > 58:
            display_title = display_title[:55] + "..."
        self.title_label.setText(display_title)

        tone = _STATUS_TONE.get(state.status, "neutral")
        self.status_pill.setText(state.status.value)
        self.status_pill.setProperty("tone", tone)
        _repolish(self.status_pill)

        created_str = state.created_at.strftime("%d/%m %H:%M")
        source_tag = "Lokal" if state.request.source_type == "local_file" else "YouTube"
        self.meta_label.setText(
            f"{created_str} • {source_tag} • {state.request.clip_count} klip • "
            f"{state.request.min_duration_sec}-{state.request.max_duration_sec}s • "
            f"{state.request.aspect_ratio}"
        )

        pct = int(round(state.progress_pct))
        self.progress_bar.setValue(pct)
        self.progress_bar.setProperty("tone", tone if tone in ("ok", "error", "warn") else "info")
        _repolish(self.progress_bar)

        self.stage_label.setText(f"Tahap: {state.current_stage} ({state.progress_pct:.0f}%)")

        is_active = state.status in _ACTIVE_STATUSES
        can_resume = state.status in (JobStatus.FAILED, JobStatus.CANCELLED)
        self.cancel_btn.setVisible(is_active)
        self.resume_btn.setVisible(can_resume)

    def mousePressEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.job_id)
        super().mousePressEvent(event)
