"""Main flat modern PyQt6 window for ATRO."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from atro import APP_NAME, __version__
from atro.config import AppSettings, load_settings, save_settings
from atro.core.schemas import (
    JobRequest,
    JobState,
    JobStatus,
    PreflightReport,
    RenderResult,
)
from atro.modules.cutter.backsound import scan_backsound_tracks
from atro.pipeline.state_store import list_all_jobs
from atro.ui.theme import build_stylesheet
from atro.ui.widgets import ClipCard, JobCard, LogViewer, SettingsDialog
from atro.ui.worker_bridge import WorkerBridge


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


class MainWindow(QMainWindow):
    """Flat modern desktop interface for the ATRO async AutoClip pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self._settings: AppSettings = load_settings()
        self._preflight: Optional[PreflightReport] = None
        self._jobs: dict[str, JobState] = {}
        self._job_cards: dict[str, JobCard] = {}
        self._selected_job_id: Optional[str] = None

        self.setWindowTitle(f"{APP_NAME} — Local AutoClip Studio")
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)

        self._bridge = WorkerBridge(settings_provider=self._get_settings, parent=self)
        self._bridge.preflight_ready.connect(self._on_preflight_ready)
        self._bridge.job_updated.connect(self._on_job_updated)
        self._bridge.log_received.connect(self._on_log_received)
        self._bridge.bridge_error.connect(self._on_bridge_error)

        self._build_ui()
        self._load_existing_jobs()

        self._bridge.start()
        self._bridge.refresh_preflight()

    def _get_settings(self) -> AppSettings:
        return self._settings

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Top Bar
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 12, 16, 12)
        top_layout.setSpacing(10)

        brand = QLabel(APP_NAME)
        brand.setObjectName("BrandTitle")
        version_pill = QLabel(f"v{__version__}")
        version_pill.setObjectName("VersionPill")
        self.subtitle_label = QLabel(f"AutoClip Pipeline • {self._settings.active_provider_summary()}")
        self.subtitle_label.setObjectName("MutedText")

        top_layout.addWidget(brand)
        top_layout.addWidget(version_pill)
        top_layout.addSpacing(4)
        top_layout.addWidget(self.subtitle_label)
        top_layout.addStretch()

        self.ytdlp_pill = QLabel("yt-dlp: ...")
        self.ytdlp_pill.setObjectName("StatusPill")
        self.ytdlp_pill.setProperty("tone", "neutral")

        self.ffmpeg_pill = QLabel("ffmpeg: ...")
        self.ffmpeg_pill.setObjectName("StatusPill")
        self.ffmpeg_pill.setProperty("tone", "neutral")

        self.apikey_pill = QLabel("API Key: ...")
        self.apikey_pill.setObjectName("StatusPill")
        self.apikey_pill.setProperty("tone", "neutral")

        self.settings_btn = QPushButton("Pengaturan")
        self.settings_btn.clicked.connect(self._open_settings)

        top_layout.addWidget(self.ytdlp_pill)
        top_layout.addWidget(self.ffmpeg_pill)
        top_layout.addWidget(self.apikey_pill)
        top_layout.addSpacing(6)
        top_layout.addWidget(self.settings_btn)
        main_layout.addWidget(top_bar)

        # 2. Startup Alert Banner (shown only on error/warning)
        self.alert_banner = QFrame()
        self.alert_banner.setObjectName("AlertBanner")
        self.alert_banner.setVisible(False)
        alert_layout = QHBoxLayout(self.alert_banner)
        alert_layout.setContentsMargins(14, 10, 14, 10)
        self.alert_label = QLabel()
        self.alert_label.setWordWrap(True)
        alert_layout.addWidget(self.alert_label, 1)
        main_layout.addWidget(self.alert_banner)

        # 3. Input Card
        input_card = QFrame()
        input_card.setObjectName("Card")
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(16, 14, 16, 14)
        input_layout.setSpacing(12)

        # Source Mode Switch Row
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)

        self.mode_youtube_btn = QPushButton("URL YouTube")
        self.mode_youtube_btn.setObjectName("ModeSwitch")
        self.mode_youtube_btn.setCheckable(True)
        self.mode_youtube_btn.setChecked(True)
        self.mode_youtube_btn.clicked.connect(lambda: self._set_source_mode("youtube_url"))

        self.mode_local_btn = QPushButton("Video Lokal")
        self.mode_local_btn.setObjectName("ModeSwitch")
        self.mode_local_btn.setCheckable(True)
        self.mode_local_btn.clicked.connect(lambda: self._set_source_mode("local_file"))

        mode_hint = QLabel("Upload video MP4/MKV/MOV dari komputer jika YouTube dibatasi")
        mode_hint.setObjectName("MutedText")

        mode_row.addWidget(self.mode_youtube_btn)
        mode_row.addWidget(self.mode_local_btn)
        mode_row.addSpacing(8)
        mode_row.addWidget(mode_hint, 1)
        input_layout.addLayout(mode_row)

        # Stacked Source Input Pages
        self.source_stack = QStackedWidget()

        # Page 0: YouTube URL
        yt_page = QWidget()
        yt_page_layout = QVBoxLayout(yt_page)
        yt_page_layout.setContentsMargins(0, 0, 0, 0)
        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Tempel URL video YouTube di sini (contoh: https://www.youtube.com/watch?v=...)")
        self.url_input.returnPressed.connect(self._submit_job)

        self.yt_start_btn = QPushButton("Mulai AutoClip")
        self.yt_start_btn.setObjectName("PrimaryButton")
        self.yt_start_btn.clicked.connect(self._submit_job)

        url_row.addWidget(self.url_input, 1)
        url_row.addWidget(self.yt_start_btn)
        yt_page_layout.addLayout(url_row)
        yt_page_layout.addStretch()

        # Page 1: Local Video Upload + Optional Subtitle
        local_page = QWidget()
        local_page_layout = QVBoxLayout(local_page)
        local_page_layout.setContentsMargins(0, 0, 0, 0)
        local_page_layout.setSpacing(8)

        local_video_row = QHBoxLayout()
        local_video_row.setSpacing(8)
        self.local_video_input = QLineEdit()
        self.local_video_input.setPlaceholderText("Pilih file video lokal (.mp4, .mkv, .mov, .webm)...")
        self.local_video_input.returnPressed.connect(self._submit_job)

        browse_video_btn = QPushButton("Pilih Video...")
        browse_video_btn.clicked.connect(self._browse_local_video)

        self.local_start_btn = QPushButton("Mulai AutoClip")
        self.local_start_btn.setObjectName("PrimaryButton")
        self.local_start_btn.clicked.connect(self._submit_job)

        local_video_row.addWidget(self.local_video_input, 1)
        local_video_row.addWidget(browse_video_btn)
        local_video_row.addWidget(self.local_start_btn)
        local_page_layout.addLayout(local_video_row)

        local_sub_row = QHBoxLayout()
        local_sub_row.setSpacing(8)
        self.local_sub_input = QLineEdit()
        self.local_sub_input.setPlaceholderText(
            "Opsional: file subtitle (.vtt / .srt) — kosongkan agar otomatis pakai faster-whisper"
        )

        browse_sub_btn = QPushButton("Pilih Subtitle")
        browse_sub_btn.setObjectName("SmallAction")
        browse_sub_btn.clicked.connect(self._browse_local_subtitle)

        clear_sub_btn = QPushButton("Hapus")
        clear_sub_btn.setObjectName("SmallAction")
        clear_sub_btn.clicked.connect(self.local_sub_input.clear)

        local_sub_row.addWidget(self.local_sub_input, 1)
        local_sub_row.addWidget(browse_sub_btn)
        local_sub_row.addWidget(clear_sub_btn)
        local_page_layout.addLayout(local_sub_row)

        self.source_stack.addWidget(yt_page)
        self.source_stack.addWidget(local_page)
        self.source_stack.setCurrentIndex(0)
        self._sync_stack_height()
        input_layout.addWidget(self.source_stack)

        # Optional parameters row
        params_row = QHBoxLayout()
        params_row.setSpacing(14)

        self.clip_count_spin = QSpinBox()
        self.clip_count_spin.setRange(1, 10)
        self.clip_count_spin.setValue(self._settings.default_clip_count)

        self.min_dur_spin = QSpinBox()
        self.min_dur_spin.setRange(5, 300)
        self.min_dur_spin.setSuffix(" s")
        self.min_dur_spin.setValue(self._settings.default_min_duration_sec)

        self.max_dur_spin = QSpinBox()
        self.max_dur_spin.setRange(10, 600)
        self.max_dur_spin.setSuffix(" s")
        self.max_dur_spin.setValue(self._settings.default_max_duration_sec)

        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(["9:16", "16:9"])
        self.aspect_combo.setCurrentText(self._settings.default_aspect_ratio)

        self.format_combo = QComboBox()
        self.format_combo.addItem("Podcast", "podcast")
        self.format_combo.addItem("Game", "game")
        fmt_idx = self.format_combo.findData(self._settings.default_edit_format)
        self.format_combo.setCurrentIndex(max(0, fmt_idx))

        self.padding_spin = QDoubleSpinBox()
        self.padding_spin.setRange(0.30, 0.50)
        self.padding_spin.setSingleStep(0.05)
        self.padding_spin.setDecimals(2)
        self.padding_spin.setSuffix(" s")
        self.padding_spin.setValue(self._settings.default_padding_sec)

        self.framing_combo = QComboBox()
        self.framing_combo.addItem("Smart Wajah", "smart_face")
        self.framing_combo.addItem("Fit Semua + Blur", "fit_all_blur")
        self.framing_combo.addItem("Center Crop", "center_crop")
        f_idx = self.framing_combo.findData(self._settings.default_framing_mode)
        self.framing_combo.setCurrentIndex(max(0, f_idx))
        self.aspect_combo.currentTextChanged.connect(lambda _t: self._sync_framing_availability())
        self.format_combo.currentIndexChanged.connect(lambda _i: self._sync_framing_availability())
        self._sync_framing_availability()

        for label_text, widget in (
            ("Jumlah Klip", self.clip_count_spin),
            ("Durasi Min", self.min_dur_spin),
            ("Durasi Max", self.max_dur_spin),
            ("Rasio Aspek", self.aspect_combo),
            ("Format", self.format_combo),
            ("Padding", self.padding_spin),
            ("Framing", self.framing_combo),
        ):
            grp = QHBoxLayout()
            grp.setSpacing(6)
            lbl = QLabel(label_text)
            lbl.setObjectName("FieldLabel")
            grp.addWidget(lbl)
            grp.addWidget(widget)
            params_row.addLayout(grp)

        params_row.addStretch()
        input_layout.addLayout(params_row)

        # Compact Viral Edit Options Row
        viral_row = QHBoxLayout()
        viral_row.setSpacing(14)
        viral_lbl = QLabel("Edit Viral")
        viral_lbl.setObjectName("FieldLabel")
        viral_row.addWidget(viral_lbl)

        self.subtitles_chk = QCheckBox("Subtitle Viral")
        self.subtitles_chk.setChecked(True)
        self.hook_title_chk = QCheckBox("Hook Judul")
        self.hook_title_chk.setChecked(True)
        self.opening_tts_chk = QCheckBox("Suara Opening")
        self.opening_tts_chk.setChecked(True)
        self.backsound_chk = QCheckBox("Backsound")
        self.backsound_chk.setChecked(True)
        self.viral_polish_chk = QCheckBox("Efek Viral")
        self.viral_polish_chk.setChecked(True)

        self.bgm_status_label = QLabel()
        self.bgm_status_label.setObjectName("MutedText")
        self._refresh_backsound_status()

        viral_row.addWidget(self.subtitles_chk)
        viral_row.addWidget(self.hook_title_chk)
        viral_row.addWidget(self.opening_tts_chk)
        viral_row.addWidget(self.backsound_chk)
        viral_row.addWidget(self.viral_polish_chk)
        viral_row.addStretch()
        viral_row.addWidget(self.bgm_status_label)
        input_layout.addLayout(viral_row)

        main_layout.addWidget(input_card)

        # 4. Split Workspace (Left: Job Queue & History | Right: Details & Logs)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)

        # Left Panel
        left_card = QFrame()
        left_card.setObjectName("Card")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        left_header = QHBoxLayout()
        left_title = QLabel("Antrean & Riwayat Job")
        left_title.setObjectName("SectionTitle")
        self.job_count_pill = QLabel("0")
        self.job_count_pill.setObjectName("VersionPill")
        left_header.addWidget(left_title)
        left_header.addStretch()
        left_header.addWidget(self.job_count_pill)
        left_layout.addLayout(left_header)

        self.jobs_scroll = QScrollArea()
        self.jobs_scroll.setWidgetResizable(True)
        self.jobs_container = QWidget()
        self.jobs_vbox = QVBoxLayout(self.jobs_container)
        self.jobs_vbox.setContentsMargins(0, 0, 4, 0)
        self.jobs_vbox.setSpacing(8)
        self.jobs_empty_label = QLabel("Belum ada job.\nMasukkan URL YouTube di atas untuk mulai memproses.")
        self.jobs_empty_label.setObjectName("MutedText")
        self.jobs_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.jobs_empty_label.setWordWrap(True)
        self.jobs_vbox.addWidget(self.jobs_empty_label)
        self.jobs_vbox.addStretch()
        self.jobs_scroll.setWidget(self.jobs_container)
        left_layout.addWidget(self.jobs_scroll, 1)

        # Right Panel
        right_card = QFrame()
        right_card.setObjectName("Card")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(10)

        detail_header = QHBoxLayout()
        self.detail_title = QLabel("Detail Job")
        self.detail_title.setObjectName("SectionTitle")
        self.open_clips_dir_btn = QPushButton("Buka Folder Klip")
        self.open_clips_dir_btn.setObjectName("SmallAction")
        self.open_clips_dir_btn.setVisible(False)
        self.open_clips_dir_btn.clicked.connect(self._open_selected_clips_dir)
        detail_header.addWidget(self.detail_title, 1)
        detail_header.addWidget(self.open_clips_dir_btn)
        right_layout.addLayout(detail_header)

        self.job_error_banner = QFrame()
        self.job_error_banner.setObjectName("AlertBanner")
        self.job_error_banner.setProperty("severity", "error")
        self.job_error_banner.setVisible(False)
        jerr_layout = QHBoxLayout(self.job_error_banner)
        jerr_layout.setContentsMargins(12, 8, 12, 8)
        self.job_error_label = QLabel()
        self.job_error_label.setWordWrap(True)
        self.job_error_label.setStyleSheet("color: #FCA5A5;")
        jerr_layout.addWidget(self.job_error_label)
        right_layout.addWidget(self.job_error_banner)

        self.tabs = QTabWidget()

        # Tab 1: Rendered Clips
        clips_tab = QWidget()
        clips_tab_layout = QVBoxLayout(clips_tab)
        clips_tab_layout.setContentsMargins(0, 0, 0, 0)
        self.clips_scroll = QScrollArea()
        self.clips_scroll.setWidgetResizable(True)
        self.clips_container = QWidget()
        self.clips_vbox = QVBoxLayout(self.clips_container)
        self.clips_vbox.setContentsMargins(0, 0, 4, 0)
        self.clips_vbox.setSpacing(10)
        self.clips_empty_label = QLabel("Pilih salah satu job untuk melihat hasil klip.")
        self.clips_empty_label.setObjectName("MutedText")
        self.clips_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clips_empty_label.setWordWrap(True)
        self.clips_vbox.addWidget(self.clips_empty_label)
        self.clips_vbox.addStretch()
        self.clips_scroll.setWidget(self.clips_container)
        clips_tab_layout.addWidget(self.clips_scroll)

        # Tab 2: Live Log
        self.log_viewer = LogViewer()

        self.tabs.addTab(clips_tab, "Hasil Klip")
        self.tabs.addTab(self.log_viewer, "Live Log")
        right_layout.addWidget(self.tabs, 1)

        splitter.addWidget(left_card)
        splitter.addWidget(right_card)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        main_layout.addWidget(splitter, 1)

    def _load_existing_jobs(self) -> None:
        existing = list_all_jobs(self._settings.workspace_dir, recover_stale=True)
        for state in existing:
            self._upsert_job(state, select=False)
        if existing:
            self._select_job(existing[0].job_id)

    def _on_preflight_ready(self, report: PreflightReport) -> None:
        self._preflight = report

        self.ytdlp_pill.setText("yt-dlp: OK" if report.yt_dlp.available else "yt-dlp: Missing")
        self.ytdlp_pill.setProperty("tone", "ok" if report.yt_dlp.available else "error")
        _repolish(self.ytdlp_pill)

        self.ffmpeg_pill.setText("ffmpeg: OK" if report.ffmpeg.available else "ffmpeg: Missing")
        self.ffmpeg_pill.setProperty("tone", "ok" if report.ffmpeg.available else "error")
        _repolish(self.ffmpeg_pill)

        self.subtitle_label.setText(f"AutoClip Pipeline • {self._settings.active_provider_summary()}")

        ai_ready, _ = self._settings.is_active_provider_ready()
        self.apikey_pill.setText("AI: Siap" if ai_ready else "AI: Belum Diisi")
        self.apikey_pill.setProperty("tone", "ok" if ai_ready else "warn")
        _repolish(self.apikey_pill)

        self._update_start_button_state()

        if report.errors:
            self.alert_banner.setProperty("severity", "error")
            _repolish(self.alert_banner)
            self.alert_label.setText("Preflight Error: " + " | ".join(report.errors))
            self.alert_label.setStyleSheet("color: #FCA5A5;")
            self.alert_banner.setVisible(True)
        elif report.warnings:
            self.alert_banner.setProperty("severity", "warning")
            _repolish(self.alert_banner)
            self.alert_label.setText("Info: " + " | ".join(report.warnings))
            self.alert_label.setStyleSheet("color: #FCD34D;")
            self.alert_banner.setVisible(True)
        else:
            self.alert_banner.setVisible(False)

    def _sync_stack_height(self) -> None:
        current = self.source_stack.currentWidget()
        if current is not None:
            self.source_stack.setFixedHeight(max(38, current.sizeHint().height()))

    def _set_source_mode(self, mode: str) -> None:
        is_local = mode == "local_file"
        self.mode_youtube_btn.setChecked(not is_local)
        self.mode_local_btn.setChecked(is_local)
        self.source_stack.setCurrentIndex(1 if is_local else 0)
        self._sync_stack_height()
        self._update_start_button_state()

    def _update_start_button_state(self) -> None:
        if self._preflight is None:
            return
        self.yt_start_btn.setEnabled(self._preflight.ok)
        self.local_start_btn.setEnabled(self._preflight.ffmpeg.available)

    def _browse_local_video(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Pilih File Video Lokal",
            "",
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi *.m4v *.flv);;All Files (*)",
        )
        if chosen:
            self.local_video_input.setText(chosen)

    def _browse_local_subtitle(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Pilih File Subtitle Opsional",
            "",
            "Subtitle Files (*.vtt *.srt);;All Files (*)",
        )
        if chosen:
            self.local_sub_input.setText(chosen)

    def _sync_framing_availability(self) -> None:
        aspect = self.aspect_combo.currentText()
        edit_fmt = self.format_combo.currentData() or "podcast"
        if aspect == "16:9":
            self.framing_combo.setEnabled(False)
            self.framing_combo.setToolTip("Rasio 16:9 memakai layout asli penuh")
        elif edit_fmt == "game":
            self.framing_combo.setEnabled(False)
            self.framing_combo.setToolTip(
                "Format Game otomatis memakai layout 40% Facecam + 60% Gameplay"
            )
        else:
            self.framing_combo.setEnabled(True)
            self.framing_combo.setToolTip("")

    def _refresh_backsound_status(self) -> None:
        tracks = scan_backsound_tracks(self._settings.backsound_dir)
        if tracks:
            self.bgm_status_label.setText(f"BGM: {len(tracks)} lagu siap")
        else:
            self.bgm_status_label.setText("BGM: folder kosong (atur di Pengaturan)")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, parent=self)
        if dlg.exec():
            self._settings = dlg.updated_settings()
            save_settings(self._settings)
            self.subtitle_label.setText(f"AutoClip Pipeline • {self._settings.active_provider_summary()}")
            self._refresh_backsound_status()
            self._bridge.refresh_preflight()

    def _submit_job(self) -> None:
        is_local = self.source_stack.currentIndex() == 1
        url: Optional[str] = None
        local_video_path: Optional[str] = None
        local_sub_path: Optional[str] = None

        if is_local:
            local_video_path = self.local_video_input.text().strip()
            if not local_video_path:
                QMessageBox.warning(self, "Video Kosong", "Pilih file video lokal terlebih dahulu.")
                return
            if not Path(local_video_path).is_file():
                QMessageBox.warning(
                    self,
                    "File Tidak Ditemukan",
                    f"File video tidak ditemukan di disk:\n{local_video_path}",
                )
                return
            sub_text = self.local_sub_input.text().strip()
            if sub_text:
                if not Path(sub_text).is_file():
                    QMessageBox.warning(
                        self,
                        "Subtitle Tidak Ditemukan",
                        f"File subtitle tidak ditemukan di disk:\n{sub_text}",
                    )
                    return
                local_sub_path = sub_text
        else:
            url = self.url_input.text().strip()
            if not url:
                QMessageBox.warning(self, "URL Kosong", "Masukkan URL YouTube terlebih dahulu.")
                return

        ai_ready, ai_msg = self._settings.is_active_provider_ready()
        if not ai_ready:
            reply = QMessageBox.question(
                self,
                "Konfigurasi AI Belum Lengkap",
                f"{ai_msg}\n\nBuka Pengaturan sekarang untuk melengkapi konfigurasi?",
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._open_settings()
                ai_ready, _ = self._settings.is_active_provider_ready()
                if not ai_ready:
                    return

        min_dur = self.min_dur_spin.value()
        max_dur = self.max_dur_spin.value()
        if max_dur <= min_dur:
            QMessageBox.warning(
                self,
                "Durasi Tidak Valid",
                "Durasi maksimum harus lebih besar daripada durasi minimum.",
            )
            return

        try:
            req = JobRequest(
                source_type="local_file" if is_local else "youtube_url",
                url=url,
                local_video_path=local_video_path,
                local_subtitle_path=local_sub_path,
                clip_count=self.clip_count_spin.value(),
                min_duration_sec=min_dur,
                max_duration_sec=max_dur,
                aspect_ratio=self.aspect_combo.currentText(),  # type: ignore[arg-type]
                padding_sec=round(self.padding_spin.value(), 2),
                edit_format=self.format_combo.currentData() or "podcast",  # type: ignore[arg-type]
                framing_mode=self.framing_combo.currentData() or "smart_face",  # type: ignore[arg-type]
                enable_subtitles=self.subtitles_chk.isChecked(),
                enable_hook_title=self.hook_title_chk.isChecked(),
                enable_opening_tts=self.opening_tts_chk.isChecked(),
                enable_backsound=self.backsound_chk.isChecked(),
                enable_viral_polish=self.viral_polish_chk.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Input Tidak Valid", str(exc))
            return

        if is_local:
            self.local_video_input.clear()
            self.local_sub_input.clear()
        else:
            self.url_input.clear()
        self._bridge.submit_request(req)

    def _on_job_updated(self, state: JobState) -> None:
        is_new = state.job_id not in self._jobs
        self._upsert_job(state, select=is_new)
        if self._selected_job_id == state.job_id:
            self._refresh_detail_panel(state)

    def _on_log_received(self, job_id: str, line: str) -> None:
        if self._selected_job_id == job_id:
            self.log_viewer.append_line(line)

    def _on_bridge_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Kesalahan Pipeline", msg)

    def _upsert_job(self, state: JobState, select: bool = False) -> None:
        self._jobs[state.job_id] = state
        self.jobs_empty_label.setVisible(False)
        self.job_count_pill.setText(str(len(self._jobs)))

        card = self._job_cards.get(state.job_id)
        if card is None:
            card = JobCard(state)
            card.clicked.connect(self._select_job)
            card.open_folder_requested.connect(self._open_job_folder)
            card.resume_requested.connect(self._bridge.resume_job)
            card.cancel_requested.connect(self._bridge.cancel_job)
            self._job_cards[state.job_id] = card
            # Insert at top (index 0) so newest jobs appear first
            self.jobs_vbox.insertWidget(0, card)
        else:
            card.update_state(state)

        if select or self._selected_job_id is None:
            self._select_job(state.job_id)

    def _select_job(self, job_id: str) -> None:
        self._selected_job_id = job_id
        for jid, card in self._job_cards.items():
            card.set_selected(jid == job_id)

        state = self._jobs.get(job_id)
        if state is not None:
            self._refresh_detail_panel(state)
            self.log_viewer.load_file(Path(state.log_path))

    def _refresh_detail_panel(self, state: JobState) -> None:
        self.detail_title.setText(f"Job {state.job_id}  •  {state.status.value}")

        if state.error:
            self.job_error_label.setText(state.error)
            self.job_error_banner.setVisible(True)
        else:
            self.job_error_banner.setVisible(False)

        clips_dir = Path(state.workspace_dir) / "clips"
        self.open_clips_dir_btn.setVisible(clips_dir.is_dir())

        # Clear existing clip cards and stretch items while preserving self.clips_empty_label
        for i in reversed(range(self.clips_vbox.count())):
            item = self.clips_vbox.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is self.clips_empty_label:
                continue
            self.clips_vbox.takeAt(i)
            if widget is not None:
                widget.deleteLater()

        render_result: Optional[RenderResult] = None
        if state.artifacts.render_result_json:
            rr_path = Path(state.artifacts.render_result_json)
            if rr_path.is_file():
                try:
                    render_result = RenderResult.model_validate_json(
                        rr_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    render_result = None

        if render_result and render_result.rendered_clips:
            self.clips_empty_label.setVisible(False)
            for clip in render_result.rendered_clips:
                self.clips_vbox.addWidget(ClipCard(clip))
        else:
            if state.status == JobStatus.COMPLETED:
                self.clips_empty_label.setText("Job selesai namun tidak ada data klip yang termuat.")
            elif state.status == JobStatus.FAILED:
                self.clips_empty_label.setText("Job berhenti karena error. Cek tab Live Log atau klik Resume.")
            elif state.status == JobStatus.CANCELLED:
                self.clips_empty_label.setText("Job dibatalkan. Klik Resume jika ingin melanjutkan.")
            else:
                self.clips_empty_label.setText(
                    f"Sedang memproses tahap '{state.current_stage}' ({state.progress_pct:.0f}%)...\n"
                    "Hasil klip akan muncul di sini setelah tahap pemotongan selesai."
                )
            self.clips_empty_label.setVisible(True)

        self.clips_vbox.addStretch()

    def _open_job_folder(self, job_id: str) -> None:
        state = self._jobs.get(job_id)
        if state:
            path = Path(state.workspace_dir)
            if path.is_dir() and os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]

    def _open_selected_clips_dir(self) -> None:
        if not self._selected_job_id:
            return
        state = self._jobs.get(self._selected_job_id)
        if state:
            clips_dir = Path(state.workspace_dir) / "clips"
            target = clips_dir if clips_dir.is_dir() else Path(state.workspace_dir)
            if target.is_dir() and os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]

    def closeEvent(self, event: Optional[QCloseEvent]) -> None:
        self._bridge.shutdown()
        if event is not None:
            event.accept()


def launch_gui() -> int:
    """Create QApplication, apply Flat Modern stylesheet, and start MainWindow."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setStyleSheet(build_stylesheet())

    window = MainWindow()
    window.show()
    return app.exec()
