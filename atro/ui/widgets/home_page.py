"""Layar 1: OpusClip + KlipDong style Home / Quick Clip page."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from atro.core.presets import READY_TEMPLATES
from atro.core.schemas import JobState, JobStatus
from atro.ui.components import (
    DurationChipBar,
    EmptyStateCard,
    ToggleSwitch,
    create_vector_icon,
)

_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}
_SUB_EXTS = {".srt", ".vtt", ".ass"}
_PROJ_THUMB_CACHE: dict[str, QPixmap] = {}


def _extract_project_thumb(job: JobState) -> Optional[QPixmap]:
    candidates: list[str] = []
    clips_dir = Path(job.workspace_dir) / "clips"
    if clips_dir.is_dir():
        first_mp4 = next(clips_dir.glob("*.mp4"), None)
        if first_mp4 is not None:
            candidates.append(str(first_mp4))
    if job.request.local_video_path:
        candidates.append(job.request.local_video_path)

    for cand in candidates:
        if not cand:
            continue
        if cand in _PROJ_THUMB_CACHE:
            return _PROJ_THUMB_CACHE[cand]
        p = Path(cand)
        if not p.is_file():
            continue
        try:
            import cv2
            from PyQt6.QtGui import QImage

            cap = cv2.VideoCapture(str(p))
            if not cap.isOpened():
                cap.release()
                continue
            total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            if total and total > 10:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(min(total * 0.1, 30)))
            ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            pix = QPixmap.fromImage(
                QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            )
            if len(_PROJ_THUMB_CACHE) > 60:
                _PROJ_THUMB_CACHE.clear()
            _PROJ_THUMB_CACHE[cand] = pix
            return pix
        except Exception:
            continue
    return None


class _ProjectThumbBanner(QWidget):
    """16:9 compact header thumbnail inside Recent Project cards."""

    def __init__(self, pix: Optional[QPixmap], source_tag: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pix = pix
        self._source_tag = source_tag
        self.setFixedHeight(108)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        if self._pix is not None and not self._pix.isNull():
            scaled = self._pix.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            sx = max(0, (scaled.width() - self.width()) // 2)
            sy = max(0, (scaled.height() - self.height()) // 2)
            p.drawPixmap(0, 0, scaled, sx, sy, self.width(), self.height())
            p.fillRect(rect, QColor(9, 10, 15, 55))
        else:
            grad = QLinearGradient(0, 0, self.width(), self.height())
            grad.setColorAt(0.0, QColor("#211A3F"))
            grad.setColorAt(1.0, QColor("#0F2430"))
            p.fillRect(rect, grad)
            p.setPen(QColor("#9CA3AF"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._source_tag)
        p.end()


class ProjectThumbnailCard(QFrame):
    """Card representing a recent project in the Home page grid."""

    clicked = pyqtSignal(str)
    studio_requested = pyqtSignal(str)

    def __init__(self, job: JobState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.job_id = job.job_id
        self.setObjectName("ProjectCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 10)
        root.setSpacing(8)

        source_tag = "FILE LOKAL" if job.request.source_type == "local_file" else "YOUTUBE"
        banner = _ProjectThumbBanner(_extract_project_thumb(job), source_tag, parent=self)
        root.addWidget(banner)

        inner = QVBoxLayout()
        inner.setContentsMargins(12, 2, 12, 2)
        inner.setSpacing(6)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)

        clip_files = list((Path(job.workspace_dir) / "clips").glob("*.mp4")) if Path(job.workspace_dir, "clips").is_dir() else []
        clip_cnt = len(clip_files) if clip_files else job.request.clip_count
        clip_badge = QLabel(f"{clip_cnt} Klip" if clip_files else f"Target {clip_cnt}")
        clip_badge.setObjectName("StatusPill")
        clip_badge.setProperty("tone", "cyan")

        status_pill = QLabel(job.status.value)
        status_pill.setObjectName("StatusPill")
        if job.status == JobStatus.COMPLETED:
            status_pill.setProperty("tone", "ok")
        elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
            status_pill.setProperty("tone", "err")
        elif job.status in (
            JobStatus.DOWNLOADING,
            JobStatus.TRANSCRIBING,
            JobStatus.ANALYZING,
            JobStatus.CUTTING,
        ):
            status_pill.setProperty("tone", "warn")

        badge_row.addWidget(clip_badge)
        badge_row.addWidget(status_pill)
        badge_row.addStretch(1)
        inner.addLayout(badge_row)

        title_text = job.request.display_source or f"Project {job.job_id[:8]}"
        if len(title_text) > 54:
            title_text = title_text[:51] + "..."
        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #F3F4F6;")
        title_lbl.setWordWrap(True)
        inner.addWidget(title_lbl)

        meta_lbl = QLabel(
            f"{job.created_at.strftime('%d %b %H:%M')} • {job.progress_pct:.0f}% • {job.request.aspect_ratio}"
        )
        meta_lbl.setObjectName("MutedText")
        inner.addWidget(meta_lbl)

        if job.status == JobStatus.COMPLETED or clip_files:
            act_row = QHBoxLayout()
            act_row.addStretch(1)
            open_studio_btn = QPushButton("Buka Studio")
            open_studio_btn.setObjectName("SmallAction")
            open_studio_btn.clicked.connect(lambda: self.studio_requested.emit(self.job_id))
            act_row.addWidget(open_studio_btn)
            inner.addLayout(act_row)

        root.addLayout(inner)

    def mousePressEvent(self, event) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.job_id)
        super().mousePressEvent(event)


class HeroDropZoneCard(QFrame):
    """Hero URL + drag & drop file area with primary CTA."""

    generate_clicked = pyqtSignal()
    file_dropped = pyqtSignal(str)
    subtitle_dropped = pyqtSignal(str)
    demo_clicked = pyqtSignal()
    browse_video_clicked = pyqtSignal()
    browse_sub_clicked = pyqtSignal()
    clear_local_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeroDropZone")
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        top_badge_row = QHBoxLayout()
        badge = QLabel("AI AUTO-CLIPPER STUDIO")
        badge.setObjectName("StepBadge")
        top_badge_row.addWidget(badge)
        top_badge_row.addStretch(1)

        demo_btn = QPushButton("Coba Demo Offline")
        demo_btn.setObjectName("GhostButton")
        demo_btn.setIcon(create_vector_icon("sparkles", "#06B6D4", 14))
        demo_btn.setToolTip("Generate video sample offline tanpa internet untuk coba Studio")
        demo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        demo_btn.clicked.connect(self.demo_clicked.emit)
        top_badge_row.addWidget(demo_btn)
        root.addLayout(top_badge_row)

        hero_title = QLabel("Ubah Video Panjang Jadi Klip Viral dalam 1 Klik")
        hero_title.setObjectName("HeroTitle")
        hero_sub = QLabel(
            "Tempel link YouTube atau seret file video (.mp4 / .mov / .mkv) langsung ke kotak ini."
        )
        hero_sub.setObjectName("MutedText")
        root.addWidget(hero_title)
        root.addWidget(hero_sub)

        # Big Hero Input + Primary CTA
        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self.url_input = QLineEdit()
        self.url_input.setObjectName("HeroInput")
        self.url_input.setPlaceholderText("Tempel URL YouTube di sini (contoh: https://youtube.com/watch?v=...)")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self.generate_clicked.emit)

        self.generate_btn = QPushButton("Generate Clips")
        self.generate_btn.setObjectName("HeroButton")
        self.generate_btn.setIcon(create_vector_icon("sparkles", "#FFFFFF", 16))
        self.generate_btn.setIconSize(QSize(16, 16))
        self.generate_btn.setMinimumWidth(168)
        self.generate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.generate_btn.setToolTip("Mulai analisa & potong klip otomatis")
        self.generate_btn.clicked.connect(self.generate_clicked.emit)

        input_row.addWidget(self.url_input, 1)
        input_row.addWidget(self.generate_btn, 0)
        root.addLayout(input_row)

        # Drag & Drop / Local File Action Row
        drop_bar = QHBoxLayout()
        drop_bar.setSpacing(8)

        self.browse_video_btn = QPushButton("Pilih / Drag Video Lokal")
        self.browse_video_btn.setObjectName("SmallAction")
        self.browse_video_btn.setIcon(create_vector_icon("upload", "#9CA3AF", 14))
        self.browse_video_btn.setToolTip("Atau drag & drop file MP4/MKV/MOV dari Explorer ke kartu ini")
        self.browse_video_btn.clicked.connect(self.browse_video_clicked.emit)

        self.browse_sub_btn = QPushButton("+ Subtitle (.srt / .vtt)")
        self.browse_sub_btn.setObjectName("SmallAction")
        self.browse_sub_btn.setToolTip("Opsional: sertakan file subtitle lokal")
        self.browse_sub_btn.clicked.connect(self.browse_sub_clicked.emit)

        self.local_file_pill = QLabel("")
        self.local_file_pill.setObjectName("StatusPill")
        self.local_file_pill.setProperty("tone", "cyan")
        self.local_file_pill.setVisible(False)

        self.clear_local_btn = QPushButton("✕ Hapus File Lokal")
        self.clear_local_btn.setObjectName("GhostButton")
        self.clear_local_btn.setVisible(False)
        self.clear_local_btn.clicked.connect(self.clear_local_clicked.emit)

        drop_bar.addWidget(self.browse_video_btn)
        drop_bar.addWidget(self.browse_sub_btn)
        drop_bar.addWidget(self.local_file_pill, 1)
        drop_bar.addWidget(self.clear_local_btn)
        drop_bar.addStretch(1)
        root.addLayout(drop_bar)

    def set_local_file_indicator(self, video_path: str, sub_path: str = "") -> None:
        vp = video_path.strip()
        sp = sub_path.strip()
        if vp or sp:
            parts = []
            if vp:
                parts.append(f"Video: {Path(vp).name}")
            if sp:
                parts.append(f"Sub: {Path(sp).name}")
            self.local_file_pill.setText("  •  ".join(parts))
            self.local_file_pill.setVisible(True)
            self.clear_local_btn.setVisible(True)
        else:
            self.local_file_pill.setVisible(False)
            self.clear_local_btn.setVisible(False)

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("dragActive", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: Optional[QDragEnterEvent]) -> None:
        if event is not None and event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                ext = Path(u.toLocalFile()).suffix.lower()
                if ext in _VIDEO_EXTS or ext in _SUB_EXTS:
                    event.acceptProposedAction()
                    self._set_drag_active(True)
                    return
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: Optional[QDropEvent]) -> None:
        self._set_drag_active(False)
        if event is not None and event.mimeData().hasUrls():
            accepted = False
            for u in event.mimeData().urls():
                fp = u.toLocalFile()
                if not fp:
                    continue
                ext = Path(fp).suffix.lower()
                if ext in _VIDEO_EXTS:
                    self.file_dropped.emit(fp)
                    accepted = True
                elif ext in _SUB_EXTS:
                    self.subtitle_dropped.emit(fp)
                    accepted = True
            if accepted:
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class HomeQuickClipPage(QWidget):
    """Layar 1: Hero Input + Quick Config Bar + Recent Projects Grid."""

    generate_requested = pyqtSignal()
    demo_requested = pyqtSignal()
    open_templates_requested = pyqtSignal()
    template_quick_selected = pyqtSignal(str)
    project_selected = pyqtSignal(str)
    project_studio_requested = pyqtSignal(str)
    duration_preset_changed = pyqtSignal(str, int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._quick_tpl_buttons: dict[str, QPushButton] = {}
        self._active_template_id = ""
        self._local_video_path = ""
        self._local_sub_path = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(28, 22, 28, 24)
        root.setSpacing(18)

        # ---- 1. Hero Input Card ----
        self.hero_card = HeroDropZoneCard(self)
        self.hero_card.generate_clicked.connect(self.generate_requested.emit)
        self.hero_card.demo_clicked.connect(self.demo_requested.emit)
        self.hero_card.file_dropped.connect(self._on_video_picked)
        self.hero_card.subtitle_dropped.connect(self._on_sub_picked)
        self.hero_card.browse_video_clicked.connect(self._browse_video)
        self.hero_card.browse_sub_clicked.connect(self._browse_sub)
        self.hero_card.clear_local_clicked.connect(self.clear_local_files)
        root.addWidget(self.hero_card)

        # ---- 2. Quick Config Bar ----
        config_card = QFrame()
        config_card.setObjectName("QuickToggleRow")
        cfg_lay = QVBoxLayout(config_card)
        cfg_lay.setContentsMargins(16, 12, 16, 12)
        cfg_lay.setSpacing(10)

        # Row A: 1-Klik Template mini-cards + Browse All Templates
        tpl_row = QHBoxLayout()
        tpl_row.setSpacing(8)
        tpl_lbl = QLabel("Template 1-Klik:")
        tpl_lbl.setObjectName("FieldLabel")
        tpl_row.addWidget(tpl_lbl)

        quick_templates = [
            ("podcast", "Podcast Focus"),
            ("ginux_clip_meme", "Viral Meme"),
            ("gaming_split", "Gaming Split"),
            ("podcast_duo_split", "Podcast Split"),
            ("twitch_comment", "Twitch Bubble"),
        ]
        for tid, label in quick_templates:
            btn = QPushButton(label)
            btn.setObjectName("SmallAction")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Aktifkan template 1-klik '{label}'")
            btn.clicked.connect(lambda _chk=False, t=tid: self._on_quick_template_clicked(t))
            self._quick_tpl_buttons[tid] = btn
            tpl_row.addWidget(btn)

        all_tpl_btn = QPushButton("Lihat Semua Template →")
        all_tpl_btn.setObjectName("GhostButton")
        all_tpl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_tpl_btn.clicked.connect(self.open_templates_requested.emit)
        tpl_row.addStretch(1)
        tpl_row.addWidget(all_tpl_btn)
        cfg_lay.addLayout(tpl_row)

        # Row B: Auto Enhance ToggleSwitches + Duration Chip Bar
        enh_row = QHBoxLayout()
        enh_row.setSpacing(14)

        enh_lbl = QLabel("Auto Enhance:")
        enh_lbl.setObjectName("FieldLabel")
        enh_row.addWidget(enh_lbl)

        self.subtitles_switch = ToggleSwitch("Subtitle", checked=True, accent_color="#7C3AED")
        self.hook_switch = ToggleSwitch("Hook Teks", checked=True, accent_color="#7C3AED")
        self.tts_switch = ToggleSwitch("Voice Intro", checked=False, accent_color="#06B6D4")
        self.backsound_switch = ToggleSwitch("Backsound", checked=True, accent_color="#7C3AED")
        self.facetrack_switch = ToggleSwitch("Face-Track", checked=True, accent_color="#06B6D4")

        for sw in (
            self.subtitles_switch,
            self.hook_switch,
            self.tts_switch,
            self.backsound_switch,
            self.facetrack_switch,
        ):
            enh_row.addWidget(sw)

        enh_row.addStretch(1)

        dur_lbl = QLabel("Durasi Klip:")
        dur_lbl.setObjectName("FieldLabel")
        enh_row.addWidget(dur_lbl)
        self.duration_bar = DurationChipBar(initial="auto")
        self.duration_bar.duration_selected.connect(self._on_duration_chip)
        enh_row.addWidget(self.duration_bar)
        cfg_lay.addLayout(enh_row)

        # Row C: Quick Target Controls (Aspect, Platform, Clip Count, Style Dropdown)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(10)

        self.template_combo = QComboBox()
        self.template_combo.setToolTip("Pilih preset gaya lengkap dari galeri")
        for tid, meta in READY_TEMPLATES.items():
            self.template_combo.addItem(str(meta.get("title", tid)), tid)
        self.template_combo.activated.connect(
            lambda: self._on_quick_template_clicked(str(self.template_combo.currentData() or "podcast"))
        )
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("YouTube Shorts", "youtube_shorts")
        self.platform_combo.addItem("TikTok / Reels", "tiktok")
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(["9:16", "16:9"])
        self.clip_count_spin = QSpinBox()
        self.clip_count_spin.setRange(1, 10)
        self.clip_count_spin.setValue(3)
        self.clip_count_spin.setPrefix("Target ")
        self.clip_count_spin.setSuffix(" klip")

        meta_row.addWidget(QLabel("Preset:"))
        meta_row.addWidget(self.template_combo, 1)
        meta_row.addWidget(QLabel("Platform:"))
        meta_row.addWidget(self.platform_combo)
        meta_row.addWidget(QLabel("Rasio:"))
        meta_row.addWidget(self.aspect_combo)
        meta_row.addWidget(self.clip_count_spin)
        meta_row.addStretch(1)
        cfg_lay.addLayout(meta_row)

        root.addWidget(config_card)

        # ---- 3. Recent Projects Grid ----
        proj_hdr = QHBoxLayout()
        proj_title = QLabel("Recent Projects")
        proj_title.setObjectName("SectionTitle")
        self.projects_count_lbl = QLabel("0 proyek")
        self.projects_count_lbl.setObjectName("MutedText")
        proj_hdr.addWidget(proj_title)
        proj_hdr.addWidget(self.projects_count_lbl)
        proj_hdr.addStretch(1)
        root.addLayout(proj_hdr)

        self.projects_empty = EmptyStateCard(
            badge_text="MULAI CEPAT",
            title="Belum ada proyek video",
            description="Tempel link YouTube di atas, drag & drop video lokal, atau klik 'Coba Demo Offline' untuk menjajal ATRO Creator Studio.",
            cta_text="Buat Demo Offline Sekarang",
        )
        self.projects_empty.action_clicked.connect(self.demo_requested.emit)
        root.addWidget(self.projects_empty)

        self.projects_grid_host = QWidget()
        self.projects_grid = QGridLayout(self.projects_grid_host)
        self.projects_grid.setContentsMargins(0, 0, 0, 0)
        self.projects_grid.setHorizontalSpacing(14)
        self.projects_grid.setVerticalSpacing(14)
        root.addWidget(self.projects_grid_host)

        root.addStretch(1)
        scroll.setWidget(container)
        outer.addWidget(scroll)

    def url_text(self) -> str:
        return self.hero_card.url_input.text().strip()

    def local_video_path(self) -> str:
        return self._local_video_path

    def local_sub_path(self) -> str:
        return self._local_sub_path

    def set_local_files(self, video_path: str, sub_path: str = "") -> None:
        self._local_video_path = video_path.strip()
        self._local_sub_path = sub_path.strip()
        self.hero_card.set_local_file_indicator(self._local_video_path, self._local_sub_path)

    def clear_local_files(self) -> None:
        self.set_local_files("", "")

    def _on_video_picked(self, path: str) -> None:
        self.set_local_files(path, self._local_sub_path)

    def _on_sub_picked(self, path: str) -> None:
        self.set_local_files(self._local_video_path, path)

    def _browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Pilih Video Lokal",
            "",
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi *.m4v);;All Files (*)",
        )
        if path:
            self._on_video_picked(path)

    def _browse_sub(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Pilih File Subtitle (Opsional)",
            "",
            "Subtitle Files (*.srt *.vtt *.ass);;All Files (*)",
        )
        if path:
            self._on_sub_picked(path)

    def _on_quick_template_clicked(self, tid: str) -> None:
        self.set_selected_template(tid)
        self.template_quick_selected.emit(tid)

    def set_selected_template(self, tid: str) -> None:
        self._active_template_id = tid
        for key, btn in self._quick_tpl_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == tid)
            btn.blockSignals(False)
        idx = self.template_combo.findData(tid)
        if idx >= 0:
            self.template_combo.blockSignals(True)
            self.template_combo.setCurrentIndex(idx)
            self.template_combo.blockSignals(False)

    def _on_duration_chip(self, preset: str) -> None:
        if preset == "30s":
            self.duration_preset_changed.emit(preset, 15, 35)
        elif preset == "60s":
            self.duration_preset_changed.emit(preset, 30, 62)
        else:
            self.duration_preset_changed.emit("auto", 20, 90)

    def set_projects(self, jobs: list[JobState]) -> None:
        while self.projects_grid.count():
            item = self.projects_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        ordered = sorted(jobs, key=lambda j: j.created_at, reverse=True)
        self.projects_count_lbl.setText(f"{len(ordered)} proyek")
        has_projects = len(ordered) > 0
        self.projects_empty.setVisible(not has_projects)
        self.projects_grid_host.setVisible(has_projects)

        cols = 3
        for idx, job in enumerate(ordered[:9]):
            card = ProjectThumbnailCard(job, parent=self.projects_grid_host)
            card.clicked.connect(self.project_selected.emit)
            card.studio_requested.connect(self.project_studio_requested.emit)
            self.projects_grid.addWidget(card, idx // cols, idx % cols)
