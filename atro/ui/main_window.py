"""WinUI 3 Fluent Step-by-Step Wizard UI for ATRO (Orange Base Theme)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QMouseEvent, QResizeEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from atro import APP_NAME, __version__
from atro.config import AppSettings, load_settings, save_settings
from atro.core.error_messages import humanize_error
from atro.core.presets import (
    CAPTION_RECIPES,
    READY_TEMPLATES,
    build_ready_template_config,
    normalize_ready_template_id,
    recommend_templates_for_context,
)
from atro.ui.components import (
    EmptyStateCard,
    FloatingActionBar,
    ToastManager,
    animate_widget_entrance,
)
from atro.ui.widgets.template_gallery import TemplateGalleryWidget
from atro.core.demo_sample import create_offline_demo_job
from atro.core.publish_kit import export_publish_kit_zip
from atro.core.schemas import (
    AnalysisResult,
    JobRequest,
    JobState,
    JobStatus,
    PreflightReport,
    RenderResult,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)
from atro.modules.cutter.backsound import scan_backsound_tracks
from atro.modules.cutter.service import apply_initial_style_overrides
from atro.modules.cutter.transitions import TRANSITION_CHOICES
from atro.pipeline.state_store import list_all_jobs
from atro.ui.theme import build_stylesheet
from atro.ui.widgets import ClipCard, ClipStudioWidget, JobCard, LogViewer, SettingsDialog
from atro.ui.widgets.clip_studio import ColorSwatchButton, ReadyTemplateCard
from atro.ui.widgets.preview_canvas import LivePreviewCanvas9x16
from atro.ui.worker_bridge import WorkerBridge

StylePresetCard = ReadyTemplateCard


def _repolish(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


class MainWindow(QMainWindow):
    """4-Step WinUI 3 Fluent interface for ATRO AutoClip & Live Composer."""

    def __init__(self) -> None:
        super().__init__()
        self._settings: AppSettings = load_settings()
        self._preflight: Optional[PreflightReport] = None
        self._jobs: dict[str, JobState] = {}
        self._job_cards: dict[str, JobCard] = {}
        self._clip_cards: list[ClipCard] = []
        self._selected_job_id: Optional[str] = None
        self._style_cards: dict[str, StylePresetCard] = {}
        self._current_step: int = 0

        self.setWindowTitle(f"{APP_NAME} — AutoClip Studio")
        self.resize(1340, 860)
        self.setMinimumSize(1020, 680)

        self._bridge = WorkerBridge(settings_provider=self._get_settings, parent=self)
        self._bridge.preflight_ready.connect(self._on_preflight_ready)
        self._bridge.job_updated.connect(self._on_job_updated)
        self._bridge.log_received.connect(self._on_log_received)
        self._bridge.bridge_error.connect(self._on_bridge_error)
        self._bridge.custom_clip_rendered.connect(self._on_custom_clip_rendered)
        self._bridge.batch_render_progress.connect(self._on_batch_render_progress)
        self._bridge.batch_clips_rendered.connect(self._on_batch_clips_rendered)
        self._bridge.standalone_clip_rendered.connect(self._on_standalone_clip_rendered)
        self._bridge.alternative_clips_generated.connect(self._on_alternative_clips_generated)

        self._toasts = ToastManager(self)
        self._build_ui()
        self.studio_widget.style_applied_to_all.connect(self._on_studio_style_applied_all)
        self._set_ui_advanced_mode(bool(self._settings.ui_advanced_mode), persist=False)
        self._load_existing_jobs()
        self.go_to_step(0)

        self._bridge.start()
        self._bridge.refresh_preflight()

    def resizeEvent(self, event: Optional[QResizeEvent]) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_toasts"):
            self._toasts.reposition()

    def _get_settings(self) -> AppSettings:
        return self._settings

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        # 1. WinUI Top Navigation & 4-Step Stepper Bar
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(14, 10, 14, 10)
        top_layout.setSpacing(10)

        brand = QLabel(APP_NAME)
        brand.setObjectName("BrandTitle")
        version_pill = QLabel(f"v{__version__}")
        version_pill.setObjectName("VersionPill")
        self.subtitle_label = QLabel(self._settings.active_provider_summary())
        self.subtitle_label.setObjectName("MutedText")

        top_layout.addWidget(brand)
        top_layout.addWidget(version_pill)
        top_layout.addWidget(self.subtitle_label)
        top_layout.addSpacing(8)

        # 4-Step Interactive Stepper Bar
        self.step_buttons: list[QPushButton] = []
        for idx, title in enumerate(
            (
                "1. Daftar Job",
                "2. Pilih Klip & Style",
                "3. Proses & Hasil",
                "4. Live Preview",
            )
        ):
            btn = QPushButton(title)
            btn.setObjectName("StepperNode")
            btn.setProperty("active", False)
            btn.setProperty("done", False)
            btn.clicked.connect(lambda _c=False, s=idx: self.go_to_step(s))
            self.step_buttons.append(btn)
            top_layout.addWidget(btn)
        self.step_btn_1, self.step_btn_2, self.step_btn_3, self.step_btn_4 = self.step_buttons

        top_layout.addStretch()

        self.health_pill = QLabel("Sistem: Cek...")
        self.health_pill.setObjectName("StatusPill")
        self.health_pill.setProperty("tone", "neutral")

        self.ytdlp_pill = QLabel("YouTube: ...")
        self.ytdlp_pill.setObjectName("StatusPill")
        self.ytdlp_pill.setProperty("tone", "neutral")

        self.ffmpeg_pill = QLabel("Mesin Video: ...")
        self.ffmpeg_pill.setObjectName("StatusPill")
        self.ffmpeg_pill.setProperty("tone", "neutral")

        self.apikey_pill = QLabel("AI: ...")
        self.apikey_pill.setObjectName("StatusPill")
        self.apikey_pill.setProperty("tone", "neutral")

        self.ui_mode_easy_btn = QPushButton("Mudah")
        self.ui_mode_easy_btn.setObjectName("ModeSwitch")
        self.ui_mode_easy_btn.setCheckable(True)
        self.ui_mode_easy_btn.clicked.connect(lambda: self._set_ui_advanced_mode(False))

        self.ui_mode_pro_btn = QPushButton("Pro")
        self.ui_mode_pro_btn.setObjectName("ModeSwitch")
        self.ui_mode_pro_btn.setCheckable(True)
        self.ui_mode_pro_btn.clicked.connect(lambda: self._set_ui_advanced_mode(True))

        self.theme_toggle_btn = QPushButton("Terang" if self._settings.ui_theme == "dark" else "Gelap")
        self.theme_toggle_btn.setObjectName("SmallAction")
        self.theme_toggle_btn.setToolTip("Ganti tema Gelap / Terang")
        self.theme_toggle_btn.clicked.connect(self._toggle_ui_theme)

        self.settings_btn = QPushButton("Pengaturan")
        self.settings_btn.setObjectName("SmallAction")
        self.settings_btn.clicked.connect(self._open_settings)

        top_layout.addWidget(self.health_pill)
        top_layout.addWidget(self.ytdlp_pill)
        top_layout.addWidget(self.ffmpeg_pill)
        top_layout.addWidget(self.apikey_pill)
        top_layout.addWidget(self.ui_mode_easy_btn)
        top_layout.addWidget(self.ui_mode_pro_btn)
        top_layout.addWidget(self.theme_toggle_btn)
        top_layout.addWidget(self.settings_btn)
        main_layout.addWidget(top_bar)

        # Startup Alert Banner
        self.alert_banner = QFrame()
        self.alert_banner.setObjectName("AlertBanner")
        self.alert_banner.setVisible(False)
        alert_layout = QHBoxLayout(self.alert_banner)
        alert_layout.setContentsMargins(14, 10, 14, 10)
        self.alert_label = QLabel()
        self.alert_label.setWordWrap(True)
        alert_layout.addWidget(self.alert_label, 1)
        main_layout.addWidget(self.alert_banner)

        # 2. 4-Step Wizard Stack
        self.wizard_stack = QStackedWidget()
        self.wizard_stack.addWidget(self._build_step1_jobs_page())
        self.wizard_stack.addWidget(self._build_step2_config_page())
        self.wizard_stack.addWidget(self._build_step3_results_page())
        self.wizard_stack.addWidget(self._build_step4_studio_page())
        main_layout.addWidget(self.wizard_stack, 1)

    # --- STEP 1: Daftar Job & Project ---
    def _build_step1_jobs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # WinUI Hero Card
        hero = QFrame()
        hero.setObjectName("HeroCard")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(20, 18, 20, 18)
        hero_layout.setSpacing(16)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(4)
        hero_title = QLabel("Buat Klip Video Viral Secara Step-by-Step")
        hero_title.setObjectName("HeroTitle")
        hero_sub = QLabel(
            "1. Pilih video & tentukan style klip di awal  •  2. Biarkan AI memotong otomatis  •  "
            "3. Sesuaikan lagi di Live Preview 9:16 jika perlu."
        )
        hero_sub.setObjectName("MutedText")
        hero_sub.setWordWrap(True)
        hero_text.addWidget(hero_title)
        hero_text.addWidget(hero_sub)
        hero_layout.addLayout(hero_text, 1)

        self.demo_sample_btn = QPushButton("Coba Video Contoh (Tanpa API Key)")
        self.demo_sample_btn.setToolTip("Buat project podcast contoh offline 3-kamera/5-pembicara untuk mencoba Studio & Publish Kit langsung")
        self.demo_sample_btn.clicked.connect(self._load_offline_demo_project)

        self.quick_studio_btn = QPushButton("Buka Studio Live Preview")
        self.quick_studio_btn.clicked.connect(lambda: self.go_to_step(3))

        self.new_project_btn = QPushButton("+ Buat Project Klip Baru ->")
        self.new_project_btn.setObjectName("HeroButton")
        self.new_project_btn.clicked.connect(lambda: self.go_to_step(1))

        hero_layout.addWidget(self.demo_sample_btn)
        hero_layout.addWidget(self.quick_studio_btn)
        hero_layout.addWidget(self.new_project_btn)
        layout.addWidget(hero)

        # Jobs List Card
        jobs_card = QFrame()
        jobs_card.setObjectName("WinUICard")
        jl = QVBoxLayout(jobs_card)
        jl.setContentsMargins(16, 14, 16, 14)
        jl.setSpacing(10)

        hdr = QHBoxLayout()
        step_badge = QLabel("Langkah 1")
        step_badge.setObjectName("StepBadge")
        title = QLabel("Daftar Job & Riwayat Pembuatan Klip")
        title.setObjectName("SectionTitle")
        self.job_count_pill = QLabel("0")
        self.job_count_pill.setObjectName("VersionPill")
        hint = QLabel("Klik salah satu job untuk melihat progres, hasil klip, atau mengedit ulang di Live Preview.")
        hint.setObjectName("MutedText")

        hdr.addWidget(step_badge)
        hdr.addWidget(title)
        hdr.addWidget(self.job_count_pill)
        hdr.addSpacing(8)
        hdr.addWidget(hint, 1)
        jl.addLayout(hdr)

        self.jobs_scroll = QScrollArea()
        self.jobs_scroll.setWidgetResizable(True)
        self.jobs_container = QWidget()
        self.jobs_vbox = QVBoxLayout(self.jobs_container)
        self.jobs_vbox.setContentsMargins(0, 0, 4, 0)
        self.jobs_vbox.setSpacing(8)
        self.jobs_empty_label = QLabel(
            "Belum ada daftar job."
        )
        self.jobs_empty_label.setVisible(False)
        self.jobs_empty_state = EmptyStateCard(
            badge_text="Mulai Cepat",
            title="Belum Ada Project Klip",
            description="Tempel link YouTube atau pilih file video podcast/gaming Anda. AI & Face Tracker ATRO akan memotong dan menyorot pembicara secara otomatis.",
            cta_text="+ Buat Project Klip Baru ->",
        )
        self.jobs_empty_state.action_clicked.connect(lambda: self.go_to_step(1))
        self.jobs_vbox.addWidget(self.jobs_empty_label)
        self.jobs_vbox.addWidget(self.jobs_empty_state)
        self.jobs_vbox.addStretch()
        self.jobs_scroll.setWidget(self.jobs_container)
        jl.addWidget(self.jobs_scroll, 1)

        layout.addWidget(jobs_card, 1)
        return page

    # --- STEP 2: Sumber Video, Jumlah Klip & Style Klip Awal ---
    def _build_step2_config_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(12)

        # Card 2A: Sumber Video
        src_card = QFrame()
        src_card.setObjectName("WinUICard")
        src_l = QVBoxLayout(src_card)
        src_l.setContentsMargins(16, 14, 16, 14)
        src_l.setSpacing(10)

        src_hdr = QHBoxLayout()
        b2a = QLabel("2A")
        b2a.setObjectName("StepBadge")
        t2a = QLabel("Pilih Sumber Video")
        t2a.setObjectName("SectionTitle")
        self.mode_youtube_btn = QPushButton("Dari Link YouTube")
        self.mode_youtube_btn.setObjectName("ModeSwitch")
        self.mode_youtube_btn.setCheckable(True)
        self.mode_youtube_btn.setChecked(True)
        self.mode_youtube_btn.clicked.connect(lambda: self._set_source_mode("youtube_url"))

        self.mode_local_btn = QPushButton("Dari File Video Komputer")
        self.mode_local_btn.setObjectName("ModeSwitch")
        self.mode_local_btn.setCheckable(True)
        self.mode_local_btn.clicked.connect(lambda: self._set_source_mode("local_file"))

        src_hdr.addWidget(b2a)
        src_hdr.addWidget(t2a)
        src_hdr.addSpacing(10)
        src_hdr.addWidget(self.mode_youtube_btn)
        src_hdr.addWidget(self.mode_local_btn)
        src_hdr.addStretch()
        src_l.addLayout(src_hdr)

        self.source_stack = QStackedWidget()

        yt_page = QWidget()
        yt_l = QHBoxLayout(yt_page)
        yt_l.setContentsMargins(0, 0, 0, 0)
        yt_l.setSpacing(10)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Tempel link video YouTube di sini (contoh: https://www.youtube.com/watch?v=...)")
        self.url_input.returnPressed.connect(self._submit_job)
        self.yt_start_btn = QPushButton("Mulai Cepat ->")
        self.yt_start_btn.setObjectName("SmallAction")
        self.yt_start_btn.clicked.connect(self._submit_job)
        yt_l.addWidget(self.url_input, 1)
        yt_l.addWidget(self.yt_start_btn)

        local_page = QWidget()
        loc_l = QVBoxLayout(local_page)
        loc_l.setContentsMargins(0, 0, 0, 0)
        loc_l.setSpacing(8)
        lv_row = QHBoxLayout()
        lv_row.setSpacing(8)
        self.local_video_input = QLineEdit()
        self.local_video_input.setPlaceholderText("Pilih file video lokal (.mp4, .mkv, .mov, .webm)...")
        self.local_video_input.returnPressed.connect(self._submit_job)
        browse_video_btn = QPushButton("Pilih Video...")
        browse_video_btn.clicked.connect(self._browse_local_video)
        self.local_start_btn = QPushButton("Mulai Cepat ->")
        self.local_start_btn.setObjectName("SmallAction")
        self.local_start_btn.clicked.connect(self._submit_job)
        lv_row.addWidget(self.local_video_input, 1)
        lv_row.addWidget(browse_video_btn)
        lv_row.addWidget(self.local_start_btn)
        loc_l.addLayout(lv_row)

        ls_row = QHBoxLayout()
        ls_row.setSpacing(8)
        self.local_sub_input = QLineEdit()
        self.local_sub_input.setPlaceholderText(
            "Opsional: file subtitle (.vtt / .srt) — biarkan kosong agar ditranskrip otomatis oleh AI"
        )
        browse_sub_btn = QPushButton("Pilih Subtitle")
        browse_sub_btn.setObjectName("SmallAction")
        browse_sub_btn.clicked.connect(self._browse_local_subtitle)
        clear_sub_btn = QPushButton("Hapus")
        clear_sub_btn.setObjectName("SmallAction")
        clear_sub_btn.clicked.connect(self.local_sub_input.clear)
        ls_row.addWidget(self.local_sub_input, 1)
        ls_row.addWidget(browse_sub_btn)
        ls_row.addWidget(clear_sub_btn)
        loc_l.addLayout(ls_row)

        self.source_stack.addWidget(yt_page)
        self.source_stack.addWidget(local_page)
        self.source_stack.setCurrentIndex(0)
        self._sync_stack_height()
        src_l.addWidget(self.source_stack)
        body_layout.addWidget(src_card)

        # Card 2B: Jumlah Klip & Durasi
        cnt_card = QFrame()
        cnt_card.setObjectName("WinUICard")
        cnt_l = QVBoxLayout(cnt_card)
        cnt_l.setContentsMargins(16, 14, 16, 14)
        cnt_l.setSpacing(10)

        cnt_hdr = QHBoxLayout()
        b2b = QLabel("2B")
        b2b.setObjectName("StepBadge")
        t2b = QLabel("Tentukan Berapa Klip & Durasi yang Diinginkan")
        t2b.setObjectName("SectionTitle")
        cnt_hdr.addWidget(b2b)
        cnt_hdr.addWidget(t2b)
        cnt_hdr.addStretch()
        cnt_l.addLayout(cnt_hdr)

        cnt_row = QHBoxLayout()
        cnt_row.setSpacing(16)
        self.clip_count_spin = QSpinBox()
        self.clip_count_spin.setRange(1, 10)
        self.clip_count_spin.setValue(self._settings.default_clip_count)

        self.min_dur_spin = QSpinBox()
        self.min_dur_spin.setRange(5, 300)
        self.min_dur_spin.setSuffix(" detik")
        self.min_dur_spin.setValue(self._settings.default_min_duration_sec)

        self.max_dur_spin = QSpinBox()
        self.max_dur_spin.setRange(10, 600)
        self.max_dur_spin.setSuffix(" detik")
        self.max_dur_spin.setValue(self._settings.default_max_duration_sec)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("YouTube Shorts (<= 60 detik, 9:16)", "youtube_shorts")
        self.platform_combo.addItem("TikTok (hingga 3 menit, 9:16)", "tiktok")
        self.platform_combo.currentIndexChanged.connect(self._on_platform_preset_changed)

        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(["9:16", "16:9"])
        self.aspect_combo.setCurrentText(self._settings.default_aspect_ratio)

        for lbl_t, w in (
            ("Platform:", self.platform_combo),
            ("Jumlah Klip:", self.clip_count_spin),
            ("Durasi Minimal:", self.min_dur_spin),
            ("Durasi Maksimal:", self.max_dur_spin),
            ("Bentuk Video:", self.aspect_combo),
        ):
            grp = QHBoxLayout()
            grp.setSpacing(6)
            lbl = QLabel(lbl_t)
            lbl.setObjectName("FieldLabel")
            grp.addWidget(lbl)
            grp.addWidget(w)
            cnt_row.addLayout(grp)
        cnt_row.addStretch()
        cnt_l.addLayout(cnt_row)
        body_layout.addWidget(cnt_card)

        # Card 2C: Pilih Style Klip di Awal (Sebelum Transcribe & Clipping)
        sty_card = QFrame()
        sty_card.setObjectName("WinUICard")
        sty_l = QVBoxLayout(sty_card)
        sty_l.setContentsMargins(16, 14, 16, 14)
        sty_l.setSpacing(12)

        sty_hdr = QHBoxLayout()
        b2c = QLabel("2C")
        b2c.setObjectName("StepBadge")
        t2c = QLabel("Pilih Style Klip Awal (Dipakai Sebelum Transcribe & Clipping)")
        t2c.setObjectName("SectionTitle")
        reassure = QLabel("Tenang: semua style ini masih bisa Anda ubah lagi di Langkah 4 (Live Preview) setelah klip jadi.")
        reassure.setObjectName("MutedText")
        sty_hdr.addWidget(b2c)
        sty_hdr.addWidget(t2c)
        sty_hdr.addSpacing(8)
        sty_hdr.addWidget(reassure, 1)
        sty_l.addLayout(sty_hdr)

        # Hidden/synced combo for compatibility
        self.format_combo = QComboBox()
        self.format_combo.addItem("Podcast", "podcast")
        self.format_combo.addItem("Game", "game")
        self.format_combo.addItem("Twitch Comment", "twitch_comment")
        self.format_combo.addItem("Subathon", "subathon")
        self.format_combo.setVisible(False)

        self.step2_gallery = TemplateGalleryWidget(
            columns=3,
            favorites=list(self._settings.favorite_templates),
            recommended=recommend_templates_for_context(edit_format=self._settings.default_edit_format),
            scroll_max_height=345,
            parent=sty_card,
        )
        self.step2_gallery.template_selected.connect(self._select_initial_style)
        self.step2_gallery.favorite_toggled.connect(self._on_step2_template_favorite_toggled)
        self._style_cards = self.step2_gallery.cards
        sty_l.addWidget(self.step2_gallery)

        self._selected_template_id: str = "podcast"
        self._syncing_step2 = False
        self._step2_sample_frame = self._build_step2_sample_frame()
        self._step2_sample_segments = [
            TranscriptSegment(
                index=0,
                start=0.0,
                end=4.2,
                text="Cara gila cuan cepat!",
                words=[
                    TranscriptWord(word="Cara", start=0.1, end=0.7),
                    TranscriptWord(word="gila", start=0.7, end=1.3),
                    TranscriptWord(word="cuan", start=1.3, end=2.2),
                    TranscriptWord(word="cepat!", start=2.2, end=4.0),
                ],
            )
        ]

        composer_split = QHBoxLayout()
        composer_split.setSpacing(14)

        left_ctrl = QWidget()
        left_l = QVBoxLayout(left_ctrl)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(10)

        self.step2_pro_drawer = QWidget()
        pro_l = QVBoxLayout(self.step2_pro_drawer)
        pro_l.setContentsMargins(0, 0, 0, 0)
        pro_l.setSpacing(8)

        # Row 1: Layout & PiP controls
        layout_row = QHBoxLayout()
        layout_row.setSpacing(10)
        self.init_layout_mode_combo = QComboBox()
        self.init_layout_mode_combo.addItem("Sesuai Kartu Preset", "")
        self.init_layout_mode_combo.addItem("1 Orang Penuh (Podcast)", "single_focus")
        self.init_layout_mode_combo.addItem("Split Atas-Bawah (Gaming)", "split_screen")
        self.init_layout_mode_combo.addItem("PiP Facecam Mengambang", "pip_facecam")
        self.init_layout_mode_combo.addItem("Bingkai + Blur (Streamer)", "blurred_frame")
        self.init_layout_mode_combo.addItem("2 Pembicara (Multi-Speaker)", "multi_speaker_split")
        self.init_layout_mode_combo.currentIndexChanged.connect(self._on_step2_style_mutated)

        self.init_pip_shape_combo = QComboBox()
        self.init_pip_shape_combo.addItem("PiP Bulat", "circle")
        self.init_pip_shape_combo.addItem("PiP Kotak", "rounded_rect")
        self.init_pip_shape_combo.currentIndexChanged.connect(self._on_step2_style_mutated)

        self.init_pip_pos_combo = QComboBox()
        self.init_pip_pos_combo.addItem("Kanan Atas", "top_right")
        self.init_pip_pos_combo.addItem("Kiri Atas", "top_left")
        self.init_pip_pos_combo.addItem("Kanan Bawah", "bottom_right")
        self.init_pip_pos_combo.addItem("Kiri Bawah", "bottom_left")
        self.init_pip_pos_combo.currentIndexChanged.connect(self._on_step2_style_mutated)

        self.init_border_col_btn = ColorSwatchButton("#F97316")
        self.init_border_col_btn.color_changed.connect(lambda _c: self._refresh_step2_preview())

        self.init_split_spin = QSpinBox()
        self.init_split_spin.setRange(25, 75)
        self.init_split_spin.setValue(40)
        self.init_split_spin.setSuffix("% Atas")
        self.init_split_spin.valueChanged.connect(lambda _v: self._refresh_step2_preview())

        for lbl_t, w in (
            ("Layout Video:", self.init_layout_mode_combo),
            ("PiP:", self.init_pip_shape_combo),
            ("Posisi PiP:", self.init_pip_pos_combo),
            ("Garis Neon:", self.init_border_col_btn),
            ("Split:", self.init_split_spin),
        ):
            lbl = QLabel(lbl_t)
            lbl.setObjectName("FieldLabel")
            layout_row.addWidget(lbl)
            layout_row.addWidget(w)
        layout_row.addStretch()
        pro_l.addLayout(layout_row)

        # Row 2: Caption Recipe (8 Creator Recipes) & Highlight/Pill Styling
        recipe_row = QHBoxLayout()
        recipe_row.setSpacing(10)
        self.init_recipe_combo = QComboBox()
        for rid, meta in CAPTION_RECIPES.items():
            self.init_recipe_combo.addItem(meta["label"], rid)
        self.init_recipe_combo.currentIndexChanged.connect(self._on_step2_recipe_changed)

        self.init_bg_mode_combo = QComboBox()
        self.init_bg_mode_combo.addItem("Tanpa Latar", "none")
        self.init_bg_mode_combo.addItem("Kapsul Kata Aktif", "active_word_pill")
        self.init_bg_mode_combo.addItem("Bar Kaca 1 Baris", "full_line_box")
        self.init_bg_mode_combo.currentIndexChanged.connect(lambda _i: self._refresh_step2_preview())

        self.init_pill_col_btn = ColorSwatchButton("#F97316")
        self.init_pill_col_btn.color_changed.connect(lambda _c: self._refresh_step2_preview())

        self.init_highlight_btn = ColorSwatchButton("#FACC15")
        self.init_highlight_btn.color_changed.connect(lambda _c: self._refresh_step2_preview())

        for lbl_t, w in (
            ("Resep Caption:", self.init_recipe_combo),
            ("Kotak Teks:", self.init_bg_mode_combo),
            ("Kapsul:", self.init_pill_col_btn),
            ("Kata Aktif:", self.init_highlight_btn),
        ):
            lbl = QLabel(lbl_t)
            lbl.setObjectName("FieldLabel")
            recipe_row.addWidget(lbl)
            recipe_row.addWidget(w)
        recipe_row.addStretch()
        pro_l.addLayout(recipe_row)

        # Row 3: Subtitle Position, Size, Words, Dual Keyword & Auto Emoji
        quick_style_row = QHBoxLayout()
        quick_style_row.setSpacing(10)
        self.init_sub_pos_combo = QComboBox()
        self.init_sub_pos_combo.addItem("Otomatis Sesuai Style", "")
        self.init_sub_pos_combo.addItem("Di Atas Subjek Bicara", "above_speaker")
        self.init_sub_pos_combo.addItem("Di Garis Tengah Split", "split_line_center")
        self.init_sub_pos_combo.addItem("Di Bagian Bawah Layar", "bottom_safe")
        self.init_sub_pos_combo.addItem("Di Bagian Atas Layar", "top_safe")
        self.init_sub_pos_combo.currentIndexChanged.connect(lambda _i: self._refresh_step2_preview())

        self.init_font_size_spin = QSpinBox()
        self.init_font_size_spin.setRange(32, 100)
        self.init_font_size_spin.setValue(66)
        self.init_font_size_spin.setSuffix(" pt")
        self.init_font_size_spin.valueChanged.connect(lambda _v: self._refresh_step2_preview())

        self.init_max_words_spin = QSpinBox()
        self.init_max_words_spin.setRange(1, 6)
        self.init_max_words_spin.setValue(4)
        self.init_max_words_spin.valueChanged.connect(lambda _v: self._refresh_step2_preview())

        self.init_dual_kw_chk = QCheckBox("Dual Keyword")
        self.init_dual_kw_chk.setChecked(True)
        self.init_dual_kw_chk.toggled.connect(lambda _v: self._refresh_step2_preview())

        self.init_sec_col_btn = ColorSwatchButton("#22C55E")
        self.init_sec_col_btn.color_changed.connect(lambda _c: self._refresh_step2_preview())

        self.init_auto_emoji_chk = QCheckBox("Auto Emoji 🔥")
        self.init_auto_emoji_chk.setChecked(True)
        self.init_auto_emoji_chk.toggled.connect(lambda _v: self._refresh_step2_preview())

        for lbl_t, w in (
            ("Letak Teks:", self.init_sub_pos_combo),
            ("Ukuran:", self.init_font_size_spin),
            ("Kata/Baris:", self.init_max_words_spin),
        ):
            lbl = QLabel(lbl_t)
            lbl.setObjectName("FieldLabel")
            quick_style_row.addWidget(lbl)
            quick_style_row.addWidget(w)
        quick_style_row.addWidget(self.init_dual_kw_chk)
        quick_style_row.addWidget(self.init_sec_col_btn)
        quick_style_row.addWidget(self.init_auto_emoji_chk)
        quick_style_row.addStretch()
        pro_l.addLayout(quick_style_row)

        # Row 4: Top Topic Pill, Vignette, Zoom & Advanced Options Toggle
        effects_row = QHBoxLayout()
        effects_row.setSpacing(10)
        self.init_zoom_punch_chk = QCheckBox("Zoom Klimaks")
        self.init_zoom_punch_chk.setChecked(False)
        self.init_zoom_punch_chk.toggled.connect(lambda _v: self._refresh_step2_preview())

        self.init_vignette_chk = QCheckBox("Vignette Sinematik")
        self.init_vignette_chk.setChecked(True)
        self.init_vignette_chk.toggled.connect(lambda _v: self._refresh_step2_preview())

        self.init_topic_hdr_chk = QCheckBox("Header Topik Atas:")
        self.init_topic_hdr_chk.setChecked(False)
        self.init_topic_hdr_chk.toggled.connect(lambda _v: self._refresh_step2_preview())

        self.init_topic_hdr_edit = QLineEdit()
        self.init_topic_hdr_edit.setPlaceholderText("Contoh: CARA CEPAT CUAN")
        self.init_topic_hdr_edit.textChanged.connect(lambda _t: self._refresh_step2_preview())

        self.adv_options_btn = QPushButton("Opsi Lanjutan...")
        self.adv_options_btn.setObjectName("SmallAction")
        self.adv_options_btn.setCheckable(True)
        self.adv_options_btn.setChecked(False)

        effects_row.addWidget(self.init_zoom_punch_chk)
        effects_row.addWidget(self.init_vignette_chk)
        effects_row.addWidget(self.init_topic_hdr_chk)
        effects_row.addWidget(self.init_topic_hdr_edit, 1)
        pro_l.addLayout(effects_row)

        # Feature Toggles Row + Progressive Disclosure button
        feat_row = QHBoxLayout()
        feat_row.setSpacing(10)
        self.subtitles_chk = QCheckBox("Subtitle Otomatis")
        self.subtitles_chk.setChecked(True)
        self.subtitles_chk.toggled.connect(lambda _v: self._refresh_step2_preview())
        self.hook_title_chk = QCheckBox("Banner Judul Hook")
        self.hook_title_chk.setChecked(True)
        self.hook_title_chk.toggled.connect(lambda _v: self._refresh_step2_preview())
        self.opening_tts_chk = QCheckBox("Suara Narator Opening")
        self.opening_tts_chk.setChecked(True)
        self.backsound_chk = QCheckBox("Musik Latar")
        self.backsound_chk.setChecked(True)
        self.viral_polish_chk = QCheckBox("Efek Warna & Progress Bar")
        self.viral_polish_chk.setChecked(True)
        self.viral_polish_chk.toggled.connect(lambda _v: self._refresh_step2_preview())
        self.bgm_status_label = QLabel()
        self.bgm_status_label.setObjectName("MutedText")
        self._refresh_backsound_status()

        for chk in (
            self.subtitles_chk,
            self.hook_title_chk,
            self.opening_tts_chk,
            self.backsound_chk,
            self.viral_polish_chk,
        ):
            feat_row.addWidget(chk)
        feat_row.addStretch()
        feat_row.addWidget(self.bgm_status_label)
        feat_row.addWidget(self.adv_options_btn)
        left_l.addLayout(feat_row)
        left_l.addWidget(self.step2_pro_drawer)

        self.adv_options_panel = QWidget()
        self.adv_options_panel.setVisible(False)
        self.adv_options_btn.toggled.connect(self.adv_options_panel.setVisible)
        self.adv_options_btn.toggled.connect(self.step2_pro_drawer.setVisible)
        self.step2_pro_drawer.setVisible(False)
        adv_l = QHBoxLayout(self.adv_options_panel)
        adv_l.setContentsMargins(0, 4, 0, 0)
        adv_l.setSpacing(12)

        self.framing_combo = QComboBox()
        self.framing_combo.addItem("Otomatis Ikut Wajah", "smart_face")
        self.framing_combo.addItem("Tampilkan Penuh + Blur", "fit_all_blur")
        self.framing_combo.addItem("Potong Tengah", "center_crop")
        f_idx = self.framing_combo.findData(self._settings.default_framing_mode)
        self.framing_combo.setCurrentIndex(max(0, f_idx))
        self.framing_combo.currentIndexChanged.connect(lambda _i: self._refresh_step2_preview())

        self.padding_spin = QDoubleSpinBox()
        self.padding_spin.setRange(0.30, 0.50)
        self.padding_spin.setSingleStep(0.05)
        self.padding_spin.setDecimals(2)
        self.padding_spin.setSuffix(" s")
        self.padding_spin.setValue(self._settings.default_padding_sec)

        self.init_transition_combo = QComboBox()
        self.init_transition_combo.addItem("Otomatis (Sesuai Pembicara)", "auto")
        for tr_val, tr_lbl in TRANSITION_CHOICES:
            self.init_transition_combo.addItem(tr_lbl, f"fixed:{tr_val}")
        self.init_transition_combo.currentIndexChanged.connect(lambda _i: self._refresh_step2_preview())

        self.init_tr_sfx_combo = QComboBox()
        self.init_tr_sfx_combo.addItem("Tanpa SFX", "none")
        self.init_tr_sfx_combo.addItem("Whoosh", "whoosh")
        self.init_tr_sfx_combo.addItem("Swipe", "swipe")
        self.init_tr_sfx_combo.addItem("Pop", "pop")
        try:
            from atro.modules.cutter.sfx import list_custom_sfx_names

            for _cn in list_custom_sfx_names():
                self.init_tr_sfx_combo.addItem(f"{_cn} (kustom)", _cn)
        except Exception:
            pass
        self.init_tr_sfx_combo.currentIndexChanged.connect(lambda _i: self._refresh_step2_preview())

        self.aspect_combo.currentTextChanged.connect(lambda _t: self._on_step2_style_mutated())
        self.format_combo.currentIndexChanged.connect(lambda _i: self._sync_framing_availability())

        adv_l.addWidget(QLabel("Fokus Kamera Podcast:"))
        adv_l.addWidget(self.framing_combo)
        adv_l.addWidget(QLabel("Transisi Kamera:"))
        adv_l.addWidget(self.init_transition_combo)
        adv_l.addWidget(self.init_tr_sfx_combo)
        adv_l.addWidget(QLabel("Jeda Potong:"))
        adv_l.addWidget(self.padding_spin)
        adv_l.addStretch()
        left_l.addWidget(self.adv_options_panel)
        left_l.addStretch()

        # Right Column: Real-Time 9:16 Live Mini-Preview
        preview_card = QFrame()
        preview_card.setObjectName("Card")
        preview_card.setFixedWidth(268)
        pv_l = QVBoxLayout(preview_card)
        pv_l.setContentsMargins(10, 10, 10, 10)
        pv_l.setSpacing(6)
        pv_hdr = QHBoxLayout()
        pv_title = QLabel("Live Mini-Preview")
        pv_title.setObjectName("SectionTitle")
        self.step2_preview_badge = QLabel("9:16")
        self.step2_preview_badge.setObjectName("StatusPill")
        pv_hdr.addWidget(pv_title, 1)
        pv_hdr.addWidget(self.step2_preview_badge)
        pv_l.addLayout(pv_hdr)

        self.step2_preview_canvas = LivePreviewCanvas9x16()
        self.step2_preview_canvas.setMinimumHeight(400)
        self.step2_preview_canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        pv_l.addWidget(self.step2_preview_canvas, 1)

        pv_hint = QLabel("Tampilan diperbarui instan setiap Anda ubah template.")
        pv_hint.setObjectName("MutedText")
        pv_hint.setWordWrap(True)
        pv_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pv_l.addWidget(pv_hint)

        composer_split.addWidget(left_ctrl, 1)
        composer_split.addWidget(preview_card)
        sty_l.addLayout(composer_split)

        body_layout.addWidget(sty_card)
        body_layout.addStretch()

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Initialize default card selection
        self._select_initial_style(self._settings.default_edit_format)

        # Step 2 Footer Navigation
        footer = QFrame()
        footer.setObjectName("Card")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 10, 14, 10)
        back_btn = QPushButton("<- Kembali ke Daftar Job")
        back_btn.clicked.connect(lambda: self.go_to_step(0))
        self.step2_submit_btn = QPushButton("Mulai Transcribe & Buat Klip Sekarang ->")
        self.step2_submit_btn.setObjectName("HeroButton")
        self.step2_submit_btn.clicked.connect(self._submit_job)
        fl.addWidget(back_btn)
        fl.addStretch()
        fl.addWidget(self.step2_submit_btn)
        outer.addWidget(footer)

        return page

    # --- STEP 3: Proses Pipeline & Hasil Klip ---
    def _build_step3_results_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Top Progress & Stage Tracker Card
        prog_card = QFrame()
        prog_card.setObjectName("WinUICard")
        pl = QVBoxLayout(prog_card)
        pl.setContentsMargins(16, 12, 16, 12)
        pl.setSpacing(10)

        hdr = QHBoxLayout()
        b3 = QLabel("Langkah 3")
        b3.setObjectName("StepBadge")
        self.detail_title = QLabel("Pilih Job dari Daftar Job")
        self.detail_title.setObjectName("SectionTitle")

        self.step3_cancel_btn = QPushButton("Batalkan Proses")
        self.step3_cancel_btn.setObjectName("DangerButton")
        self.step3_cancel_btn.setVisible(False)
        self.step3_cancel_btn.clicked.connect(self._cancel_selected_job)

        self.step3_resume_btn = QPushButton("Lanjutkan (Resume)")
        self.step3_resume_btn.setObjectName("SmallAction")
        self.step3_resume_btn.setVisible(False)
        self.step3_resume_btn.clicked.connect(self._resume_selected_job)

        self.open_clips_dir_btn = QPushButton("Buka Folder Hasil")
        self.open_clips_dir_btn.setObjectName("SmallAction")
        self.open_clips_dir_btn.setVisible(False)
        self.open_clips_dir_btn.clicked.connect(self._open_selected_clips_dir)

        self.toggle_log_btn = QPushButton("Lihat Log Proses")
        self.toggle_log_btn.setObjectName("SmallAction")
        self.toggle_log_btn.setCheckable(True)
        self.toggle_log_btn.setChecked(False)

        back_jobs_btn = QPushButton("<- Daftar Job")
        back_jobs_btn.setObjectName("SmallAction")
        back_jobs_btn.clicked.connect(lambda: self.go_to_step(0))

        hdr.addWidget(back_jobs_btn)
        hdr.addWidget(b3)
        hdr.addWidget(self.detail_title, 1)
        hdr.addWidget(self.step3_cancel_btn)
        hdr.addWidget(self.step3_resume_btn)
        hdr.addWidget(self.open_clips_dir_btn)
        hdr.addWidget(self.toggle_log_btn)
        pl.addLayout(hdr)

        # 4 Visual Pipeline Stages
        stage_row = QHBoxLayout()
        stage_row.setSpacing(8)
        self.stage_nodes: list[QLabel] = []
        for st_lbl in (
            "1. Download / Baca Video",
            "2. Transcribe Suara",
            "3. Kurasi Klip AI",
            "4. Render Style Klip",
        ):
            lbl = QLabel(st_lbl)
            lbl.setObjectName("StageNode")
            lbl.setProperty("state", "idle")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stage_nodes.append(lbl)
            stage_row.addWidget(lbl, 1)
        pl.addLayout(stage_row)

        self.step3_progress_bar = QProgressBar()
        self.step3_progress_bar.setRange(0, 100)
        self.step3_progress_bar.setTextVisible(False)
        pl.addWidget(self.step3_progress_bar)
        layout.addWidget(prog_card)

        self.job_error_banner = QFrame()
        self.job_error_banner.setObjectName("AlertBanner")
        self.job_error_banner.setProperty("severity", "error")
        self.job_error_banner.setVisible(False)
        jerr_layout = QHBoxLayout(self.job_error_banner)
        jerr_layout.setContentsMargins(12, 8, 12, 8)
        self.job_error_label = QLabel()
        self.job_error_label.setWordWrap(True)
        jerr_layout.addWidget(self.job_error_label)
        layout.addWidget(self.job_error_banner)

        # Collapsible Live Log Drawer
        self.log_viewer = LogViewer()
        self.log_viewer.setMaximumHeight(180)
        self.log_viewer.setVisible(False)
        self.toggle_log_btn.toggled.connect(self.log_viewer.setVisible)
        layout.addWidget(self.log_viewer)

        # Rendered Clips Card
        clips_card = QFrame()
        clips_card.setObjectName("WinUICard")
        cl = QVBoxLayout(clips_card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        clips_hdr = QHBoxLayout()
        clips_title = QLabel("Hasil Klip Video (Centang beberapa/semua klip untuk edit massal, atau klik 'Ubah Style & Live Preview ->')")
        clips_title.setObjectName("SectionTitle")
        self.alt_clips_btn = QPushButton("Buat Klip Alternatif")
        self.alt_clips_btn.setObjectName("SmallAction")
        self.alt_clips_btn.setToolTip("Minta AI mencari momen baru yang berbeda tanpa transcribe ulang")
        self.alt_clips_btn.clicked.connect(self._generate_alternative_clips)
        self.alt_clips_btn.setVisible(False)
        clips_hdr.addWidget(clips_title, 1)
        clips_hdr.addWidget(self.alt_clips_btn)
        cl.addLayout(clips_hdr)

        self.topic_outline_label = QLabel("")
        self.topic_outline_label.setObjectName("MutedText")
        self.topic_outline_label.setWordWrap(True)
        self.topic_outline_label.setVisible(False)
        cl.addWidget(self.topic_outline_label)

        self.clips_scroll = QScrollArea()
        self.clips_scroll.setWidgetResizable(True)
        self.clips_container = QWidget()
        self.clips_vbox = QVBoxLayout(self.clips_container)
        self.clips_vbox.setContentsMargins(0, 0, 4, 0)
        self.clips_vbox.setSpacing(10)
        self.clips_empty_label = QLabel("Pilih salah satu job dari Langkah 1 atau buat project klip baru.")
        self.clips_empty_label.setObjectName("MutedText")
        self.clips_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clips_empty_label.setWordWrap(True)
        self.clips_vbox.addWidget(self.clips_empty_label)
        self.clips_vbox.addStretch()
        self.clips_scroll.setWidget(self.clips_container)
        cl.addWidget(self.clips_scroll, 1)

        self.floating_batch_bar = FloatingActionBar()
        fb_l = self.floating_batch_bar.bar_layout()
        self.select_all_clips_btn = QPushButton("Pilih Semua")
        self.select_all_clips_btn.setObjectName("SmallAction")
        self.select_all_clips_btn.clicked.connect(lambda: self._select_all_step3_clips(True))

        self.deselect_all_clips_btn = QPushButton("Batal Pilih")
        self.deselect_all_clips_btn.setObjectName("SmallAction")
        self.deselect_all_clips_btn.clicked.connect(lambda: self._select_all_step3_clips(False))

        self.selected_clips_pill = QLabel("0 klip dipilih")
        self.selected_clips_pill.setObjectName("VersionPill")

        self.batch_studio_btn = QPushButton("Edit di Studio ->")
        self.batch_studio_btn.setObjectName("SmallAction")
        self.batch_studio_btn.clicked.connect(self._open_batch_in_studio)

        self.batch_rerender_btn = QPushButton("Render Ulang Terpilih")
        self.batch_rerender_btn.setObjectName("PrimaryButton")
        self.batch_rerender_btn.clicked.connect(self._rerender_selected_step3_clips)

        fb_l.addWidget(self.select_all_clips_btn)
        fb_l.addWidget(self.deselect_all_clips_btn)
        fb_l.addWidget(self.selected_clips_pill)
        fb_l.addStretch()
        fb_l.addWidget(self.batch_studio_btn)
        fb_l.addWidget(self.batch_rerender_btn)
        self.floating_batch_bar.setVisible(False)
        cl.addWidget(self.floating_batch_bar)

        layout.addWidget(clips_card, 1)
        return page

    # --- STEP 4: Full-Window Live Preview & Edit Akhir ---
    def _build_step4_studio_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        nav = QFrame()
        nav.setObjectName("WinUICard")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(12, 8, 12, 8)
        nl.setSpacing(10)

        back_results_btn = QPushButton("<- Kembali ke Hasil Klip")
        back_results_btn.setObjectName("SmallAction")
        back_results_btn.clicked.connect(lambda: self.go_to_step(2))

        back_jobs_btn = QPushButton("Daftar Job")
        back_jobs_btn.setObjectName("SmallAction")
        back_jobs_btn.clicked.connect(lambda: self.go_to_step(0))

        b4 = QLabel("Langkah 4")
        b4.setObjectName("StepBadge")
        t4 = QLabel("Live Preview 9:16 & Ubah Style Klip (Sesuaikan Tata Letak, Subtitle, Overlay & Audio)")
        t4.setObjectName("SectionTitle")

        nl.addWidget(back_results_btn)
        nl.addWidget(back_jobs_btn)
        nl.addWidget(b4)
        nl.addWidget(t4, 1)
        layout.addWidget(nav)

        self.studio_widget = ClipStudioWidget(settings_provider=self._get_settings)
        self.studio_widget.render_requested.connect(self._on_studio_render_requested)
        self.studio_widget.batch_render_requested.connect(self._on_studio_batch_render_requested)
        self.studio_widget.standalone_render_requested.connect(
            self._on_studio_standalone_render_requested
        )
        layout.addWidget(self.studio_widget, 1)
        return page

    # --- Wizard Step & Style Logic ---
    def go_to_step(self, step_idx: int) -> None:
        """Switch wizard page cleanly with smooth fade transition & update stepper indicators."""
        clamped = max(0, min(3, int(step_idx)))
        changed = clamped != self._current_step
        self._current_step = clamped
        self.wizard_stack.setCurrentIndex(clamped)
        for idx, btn in enumerate(self.step_buttons):
            btn.setProperty("active", idx == clamped)
            btn.setProperty("done", idx < clamped)
            _repolish(btn)
        if changed:
            animate_widget_entrance(
                self.wizard_stack.currentWidget(),
                duration_ms=170,
                enabled=not self._settings.ui_reduce_motion,
            )
        if clamped == 3 and hasattr(self, "studio_widget"):
            self.studio_widget._maybe_schedule_auto_detect()

    def _set_ui_advanced_mode(self, is_pro: bool, persist: bool = True) -> None:
        """Toggle ATRO between clean Easy (Mudah) mode and full Pro controls."""
        self.ui_mode_easy_btn.setChecked(not is_pro)
        self.ui_mode_pro_btn.setChecked(is_pro)
        self.health_pill.setVisible(not is_pro)
        self.ytdlp_pill.setVisible(is_pro)
        self.ffmpeg_pill.setVisible(is_pro)
        self.apikey_pill.setVisible(is_pro)
        if hasattr(self, "step2_pro_drawer"):
            self.step2_pro_drawer.setVisible(is_pro or self.adv_options_btn.isChecked())
        if hasattr(self, "adv_options_panel"):
            self.adv_options_panel.setVisible(is_pro or self.adv_options_btn.isChecked())
        if hasattr(self, "studio_widget"):
            self.studio_widget.set_advanced_mode(is_pro)
        if persist and self._settings.ui_advanced_mode != is_pro:
            self._settings.ui_advanced_mode = is_pro
            save_settings(self._settings)

    def _toggle_ui_theme(self) -> None:
        """Switch seamlessly between Dark and Light Fluent theme."""
        new_mode = "light" if self._settings.ui_theme == "dark" else "dark"
        self._settings.ui_theme = new_mode  # type: ignore[assignment]
        save_settings(self._settings)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(new_mode))  # type: ignore[arg-type]
        self.theme_toggle_btn.setText("Terang" if new_mode == "dark" else "Gelap")
        self._toasts.show_toast(
            "Tema Diperbarui",
            f"Tampilan beralih ke mode {'Gelap' if new_mode == 'dark' else 'Terang'}.",
            tone="info",
            reduce_motion=self._settings.ui_reduce_motion,
        )

    def _select_initial_style(self, format_id: str) -> None:
        tid = normalize_ready_template_id(format_id)
        self._selected_template_id = tid
        meta = READY_TEMPLATES[tid]
        job_fmt = str(meta["job_edit_format"])

        for cid, card in self._style_cards.items():
            card.set_selected(cid == tid)
        if hasattr(self, "step2_gallery"):
            self.step2_gallery.set_selected(tid)

        self._syncing_step2 = True
        idx = self.format_combo.findData(job_fmt)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)

        cfg = build_ready_template_config(template_id=tid, clip_title="Rahasia Cuan Viral")
        self.init_layout_mode_combo.setCurrentIndex(0)
        self.init_pip_shape_combo.setCurrentIndex(max(0, self.init_pip_shape_combo.findData(cfg.layout.pip_shape)))
        self.init_pip_pos_combo.setCurrentIndex(max(0, self.init_pip_pos_combo.findData(cfg.layout.pip_position)))
        self.init_border_col_btn.set_color_hex(cfg.layout.frame_border_color_hex)

        rec_idx = self.init_recipe_combo.findData(cfg.subtitles.caption_recipe)
        self.init_recipe_combo.setCurrentIndex(max(0, rec_idx))
        bgm_idx = self.init_bg_mode_combo.findData(cfg.subtitles.caption_bg_mode)
        self.init_bg_mode_combo.setCurrentIndex(max(0, bgm_idx))
        self.init_pill_col_btn.set_color_hex(cfg.subtitles.active_word_bg_hex)
        self.init_highlight_btn.set_color_hex(cfg.subtitles.highlight_color_hex)
        self.init_font_size_spin.setValue(cfg.subtitles.font_size)
        self.init_max_words_spin.setValue(cfg.subtitles.max_words_per_line)
        self.init_dual_kw_chk.setChecked(cfg.subtitles.dual_keyword_enabled)
        self.init_sec_col_btn.set_color_hex(cfg.subtitles.secondary_keyword_color_hex)
        self.init_auto_emoji_chk.setChecked(cfg.subtitles.auto_emoji_enabled)

        self.init_vignette_chk.setChecked(cfg.overlays.climax_effects.vignette_enabled)
        self.init_topic_hdr_chk.setChecked(cfg.overlays.climax_effects.topic_header_enabled)
        self.init_topic_hdr_edit.setText(cfg.overlays.climax_effects.topic_header_text)

        self.init_sub_pos_combo.setCurrentIndex(
            max(0, self.init_sub_pos_combo.findData(cfg.subtitles.placement_preset))
        )
        self.init_split_spin.setValue(int(round(cfg.layout.split_ratio_top * 100)))
        self.init_zoom_punch_chk.setChecked(cfg.overlays.climax_effects.zoom_punch_enabled)
        tr = cfg.layout.transition
        tr_key = "auto" if tr.mode == "auto" else f"fixed:{tr.type}"
        self.init_transition_combo.setCurrentIndex(max(0, self.init_transition_combo.findData(tr_key)))
        self.init_tr_sfx_combo.setCurrentIndex(max(0, self.init_tr_sfx_combo.findData(tr.sfx)))

        self._syncing_step2 = False
        self._sync_framing_availability()
        self._refresh_step2_preview()

    @staticmethod
    def _build_step2_sample_frame() -> np.ndarray:
        """Create a vivid 1280x720 dual-zone preview backdrop so layout differences are crystal clear."""
        w, h = 1280, 720
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        x_grad = np.linspace(22, 64, w, dtype=np.uint8)[None, :]
        y_grad = np.linspace(18, 52, h, dtype=np.uint8)[:, None]
        frame[:, :, 0] = np.clip(x_grad + 20, 0, 255)
        frame[:, :, 1] = np.clip(y_grad + 14, 0, 255)
        frame[:, :, 2] = 42
        # Left zone: illuminated speaker / facecam silhouette
        cv2.rectangle(frame, (40, 45), (520, 420), (36, 46, 78), -1)
        cv2.circle(frame, (265, 195), 76, (90, 148, 224), -1)
        cv2.ellipse(frame, (265, 345), (125, 78), 0, 180, 360, (66, 114, 210), -1)
        cv2.putText(frame, "FACECAM", (170, 405), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (240, 248, 255), 2)
        # Right/Main zone: bright stylized gameplay / screen scene
        cv2.rectangle(frame, (540, 45), (1235, 675), (28, 86, 62), -1)
        cv2.rectangle(frame, (600, 120), (1170, 580), (44, 122, 92), 3)
        cv2.circle(frame, (885, 355), 110, (56, 189, 248), 4)
        cv2.putText(frame, "GAMEPLAY / MAIN SCREEN", (655, 368), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (250, 204, 21), 3)
        return frame

    def _on_step2_style_mutated(self) -> None:
        if self._syncing_step2:
            return
        self._sync_framing_availability()
        self._refresh_step2_preview()

    def _on_step2_recipe_changed(self) -> None:
        if self._syncing_step2:
            return
        rid = str(self.init_recipe_combo.currentData() or "hormozi_viral")
        recipe = CAPTION_RECIPES.get(rid, CAPTION_RECIPES["hormozi_viral"])
        self._syncing_step2 = True
        self.init_bg_mode_combo.setCurrentIndex(
            max(0, self.init_bg_mode_combo.findData(recipe["caption_bg_mode"]))
        )
        self.init_pill_col_btn.set_color_hex(recipe["active_word_bg_hex"])
        self.init_highlight_btn.set_color_hex(recipe["highlight_color_hex"])
        self.init_dual_kw_chk.setChecked(bool(recipe["dual_keyword_enabled"]))
        self.init_sec_col_btn.set_color_hex(recipe["secondary_keyword_color_hex"])
        self.init_auto_emoji_chk.setChecked(bool(recipe["auto_emoji_enabled"]))
        self.init_font_size_spin.setValue(int(recipe["font_size"]))
        self.init_max_words_spin.setValue(int(recipe["max_words_per_line"]))
        self._syncing_step2 = False
        self._refresh_step2_preview()

    def _refresh_step2_preview(self) -> None:
        if getattr(self, "_syncing_step2", False) or not hasattr(self, "step2_preview_canvas"):
            return
        edit_fmt = str(self.format_combo.currentData() or "podcast")
        tid = getattr(self, "_selected_template_id", edit_fmt)
        cfg = build_ready_template_config(
            template_id=tid,
            clip_title="Rahasia Cuan Cepat!",
            start_time=0.0,
            end_time=12.0,
            padding_sec=0.0,
        )
        overrides = self._collect_initial_style_overrides(edit_fmt)
        apply_initial_style_overrides(cfg, overrides)

        aspect = self.aspect_combo.currentText()
        cfg.aspect_ratio = aspect  # type: ignore[assignment]
        if aspect == "16:9":
            cfg.canvas_width, cfg.canvas_height = 1920, 1080
        cfg.subtitles.enabled = self.subtitles_chk.isChecked()
        cfg.overlays.hook_banner.enabled = self.hook_title_chk.isChecked()
        cfg.overlays.climax_effects.progress_bar_enabled = self.viral_polish_chk.isChecked()
        cfg.overlays.climax_effects.contrast_boost_enabled = self.viral_polish_chk.isChecked()
        cfg.sync_subtitle_anchor()
        if cfg.subtitles.placement_preset == "above_speaker" and not cfg.layout.shot_crops:
            from atro.core.composer_schema import CropBoxConfig, FaceAnchorBox, ShotCropSegment

            cfg.layout.shot_crops = [
                ShotCropSegment(
                    t_start=0.0,
                    t_end=12.0,
                    top_crop=CropBoxConfig(auto_detect=True, x=0.04, y=0.0, w=0.34, h=1.0),
                    speaker_id="SPK_1",
                    speaker_faces={"SPK_1": FaceAnchorBox(x=0.14, y=0.16, w=0.14, h=0.22, confidence=0.95)},
                    confidence=0.95,
                )
            ]

        self.step2_preview_badge.setText(f"{aspect} • {cfg.layout.mode}")
        self.step2_preview_canvas.render_state(
            source_frame_bgr=self._step2_sample_frame,
            config=cfg,
            rel_time_sec=1.6,
            segments=self._step2_sample_segments,
            clip_start_sec=0.0,
            clip_end_sec=12.0,
        )

    def _on_step2_template_favorite_toggled(self, template_id: str, is_fav: bool) -> None:
        favs = list(self._settings.favorite_templates)
        if is_fav and template_id not in favs:
            favs.append(template_id)
        elif not is_fav and template_id in favs:
            favs.remove(template_id)
        self._settings.favorite_templates = favs
        save_settings(self._settings)
        if hasattr(self.studio_widget, "template_gallery"):
            self.studio_widget.template_gallery.set_favorites(favs)

    # --- Existing Pipeline & State Handlers ---
    def _load_existing_jobs(self) -> None:
        existing = list_all_jobs(self._settings.workspace_dir, recover_stale=True)
        for state in existing:
            self._upsert_job(state, select=False)
        if existing:
            self._select_job(existing[0].job_id)

    def _on_preflight_ready(self, report: PreflightReport) -> None:
        self._preflight = report

        self.ytdlp_pill.setText("YouTube: Siap" if report.yt_dlp.available else "YouTube: Belum Ada")
        self.ytdlp_pill.setProperty("tone", "ok" if report.yt_dlp.available else "error")
        _repolish(self.ytdlp_pill)

        self.ffmpeg_pill.setText("Mesin Video: Siap" if report.ffmpeg.available else "Mesin Video: Belum Ada")
        self.ffmpeg_pill.setProperty("tone", "ok" if report.ffmpeg.available else "error")
        _repolish(self.ffmpeg_pill)

        self.subtitle_label.setText(self._settings.active_provider_summary())

        ai_ready, _ = self._settings.is_active_provider_ready()
        self.apikey_pill.setText("AI: Siap" if ai_ready else "AI: Belum Diisi")
        self.apikey_pill.setProperty("tone", "ok" if ai_ready else "warn")
        _repolish(self.apikey_pill)

        all_ok = report.ffmpeg.available and report.ffprobe.available and ai_ready
        if all_ok:
            self.health_pill.setText("Sistem: Siap")
            self.health_pill.setProperty("tone", "ok")
        elif not report.ffmpeg.available:
            self.health_pill.setText("Sistem: Cek FFmpeg")
            self.health_pill.setProperty("tone", "error")
        else:
            self.health_pill.setText("Sistem: Cek Pengaturan")
            self.health_pill.setProperty("tone", "warn")
        self.health_pill.setToolTip(
            f"YouTube: {'Siap' if report.yt_dlp.available else 'Belum Ada'} • "
            f"FFmpeg: {'Siap' if report.ffmpeg.available else 'Belum Ada'} • "
            f"AI: {'Siap' if ai_ready else 'Belum Diisi'}"
        )
        _repolish(self.health_pill)

        self._update_start_button_state()

        if report.errors:
            self.alert_banner.setProperty("severity", "error")
            _repolish(self.alert_banner)
            self.alert_label.setText("Preflight Error: " + " | ".join(report.errors))
            self.alert_banner.setVisible(True)
        elif report.warnings:
            self.alert_banner.setProperty("severity", "warning")
            _repolish(self.alert_banner)
            self.alert_label.setText("Info: " + " | ".join(report.warnings))
            self.alert_banner.setVisible(True)
        else:
            self.alert_banner.setVisible(False)

    def _sync_stack_height(self) -> None:
        current = self.source_stack.currentWidget()
        if current is not None:
            self.source_stack.setFixedHeight(max(42, current.sizeHint().height()))

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
        is_local = self.source_stack.currentIndex() == 1
        self.yt_start_btn.setEnabled(self._preflight.ok)
        self.local_start_btn.setEnabled(self._preflight.ffmpeg.available)
        self.step2_submit_btn.setEnabled(
            self._preflight.ffmpeg.available if is_local else self._preflight.ok
        )

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
        tid = getattr(self, "_selected_template_id", "podcast")
        tpl_layout = str(READY_TEMPLATES.get(tid, READY_TEMPLATES["podcast"])["layout_mode"])
        custom_lm = str(self.init_layout_mode_combo.currentData() or "") if hasattr(self, "init_layout_mode_combo") else ""
        effective_lm = custom_lm or tpl_layout
        effective_split = effective_lm in ("split_screen", "multi_speaker_split")
        effective_pip = effective_lm == "pip_facecam"
        if hasattr(self, "init_split_spin"):
            self.init_split_spin.setEnabled(effective_split)
        if hasattr(self, "init_pip_shape_combo"):
            self.init_pip_shape_combo.setEnabled(effective_pip)
            self.init_pip_pos_combo.setEnabled(effective_pip)

        if aspect == "16:9":
            self.framing_combo.setEnabled(False)
            self.framing_combo.setToolTip("Bentuk 16:9 memakai layar penuh asli")
        elif effective_lm != "single_focus":
            self.framing_combo.setEnabled(False)
            self.framing_combo.setToolTip("Style ini memakai tata letak khusus")
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
            self.subtitle_label.setText(self._settings.active_provider_summary())
            self._refresh_backsound_status()
            self._bridge.refresh_preflight()

    def _collect_initial_style_overrides(self, edit_format: str) -> dict:
        tid = getattr(self, "_selected_template_id", normalize_ready_template_id(edit_format))
        tpl_layout = str(READY_TEMPLATES.get(tid, READY_TEMPLATES["podcast"])["layout_mode"])
        overrides: dict = {
            "ready_template_id": tid,
            "caption_recipe": str(self.init_recipe_combo.currentData() or "hormozi_viral"),
            "caption_bg_mode": str(self.init_bg_mode_combo.currentData() or "none"),
            "active_word_bg_hex": self.init_pill_col_btn.color_hex(),
            "dual_keyword_enabled": self.init_dual_kw_chk.isChecked(),
            "secondary_keyword_color_hex": self.init_sec_col_btn.color_hex(),
            "auto_emoji_enabled": self.init_auto_emoji_chk.isChecked(),
            "subtitle_highlight_hex": self.init_highlight_btn.color_hex(),
            "subtitle_font_size": int(self.init_font_size_spin.value()),
            "subtitle_max_words": int(self.init_max_words_spin.value()),
            "pip_shape": str(self.init_pip_shape_combo.currentData() or "circle"),
            "pip_position": str(self.init_pip_pos_combo.currentData() or "top_right"),
            "frame_border_color_hex": self.init_border_col_btn.color_hex(),
            "zoom_punch_enabled": self.init_zoom_punch_chk.isChecked(),
            "vignette_enabled": self.init_vignette_chk.isChecked(),
            "topic_header_enabled": self.init_topic_hdr_chk.isChecked(),
            "topic_header_text": self.init_topic_hdr_edit.text().strip(),
            "framing_mode": str(self.framing_combo.currentData() or "smart_face"),
        }
        custom_lm = str(self.init_layout_mode_combo.currentData() or "")
        if custom_lm:
            overrides["layout_mode"] = custom_lm
        place = self.init_sub_pos_combo.currentData()
        if place:
            overrides["subtitle_placement"] = place
        effective_lm = custom_lm or tpl_layout
        if effective_lm in ("split_screen", "multi_speaker_split"):
            overrides["split_ratio_top"] = round(self.init_split_spin.value() / 100.0, 2)
        tr_sel = str(self.init_transition_combo.currentData() or "auto")
        if tr_sel == "auto":
            overrides["transition_mode"] = "auto"
        elif tr_sel.startswith("fixed:"):
            overrides["transition_mode"] = "fixed"
            overrides["transition_type"] = tr_sel.split(":", 1)[1]
        overrides["transition_sfx"] = str(self.init_tr_sfx_combo.currentData() or "none")
        return overrides

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

        edit_fmt = self.format_combo.currentData() or "podcast"
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
                edit_format=edit_fmt,  # type: ignore[arg-type]
                framing_mode=self.framing_combo.currentData() or "smart_face",  # type: ignore[arg-type]
                platform_target=self.platform_combo.currentData() or "youtube_shorts",  # type: ignore[arg-type]
                enable_subtitles=self.subtitles_chk.isChecked(),
                enable_hook_title=self.hook_title_chk.isChecked(),
                enable_opening_tts=self.opening_tts_chk.isChecked(),
                enable_backsound=self.backsound_chk.isChecked(),
                enable_viral_polish=self.viral_polish_chk.isChecked(),
                initial_style_overrides=self._collect_initial_style_overrides(edit_fmt),
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
        self.go_to_step(2)

    def _on_job_updated(self, state: JobState) -> None:
        is_new = state.job_id not in self._jobs
        self._upsert_job(state, select=is_new)
        if self._selected_job_id == state.job_id:
            self._refresh_detail_panel(state)

    def _on_log_received(self, job_id: str, line: str) -> None:
        if self._selected_job_id == job_id:
            self.log_viewer.append_line(line)

    def _open_clip_in_studio(self, clip_index: int) -> None:
        if not self._selected_job_id:
            return
        state = self._jobs.get(self._selected_job_id)
        if state is not None:
            self.studio_widget.load_job_context(state, select_clip_index=clip_index)
            self.go_to_step(3)

    def _set_studio_render_busy(self, busy: bool, label_text: Optional[str] = None) -> None:
        self.studio_widget.export_btn.setEnabled(not busy)
        self.studio_widget.batch_export_btn.setEnabled(not busy)
        self.studio_widget.apply_style_all_btn.setEnabled(not busy)
        if hasattr(self, "batch_rerender_btn"):
            self.batch_rerender_btn.setEnabled((not busy) and bool(self.selected_step3_clip_indices()))
        if busy:
            self.studio_widget.export_btn.setText(label_text or "Sedang Render FFmpeg...")
        else:
            self.studio_widget.export_btn.setText("Simpan & Render Klip Ini")
            self.studio_widget.batch_export_btn.setText("Edit & Render Beberapa/Semua...")

    def _on_studio_render_requested(self, job_id: str, config: object) -> None:
        from atro.core.composer_schema import ClipComposerConfig

        if isinstance(config, ClipComposerConfig):
            self._set_studio_render_busy(True, "Sedang Render FFmpeg...")
            self._bridge.render_custom_clip(job_id, config)

    def _on_studio_batch_render_requested(
        self,
        job_id: str,
        clip_indices: object,
        style_config: object,
        force_redetect_faces: bool,
    ) -> None:
        from atro.core.composer_schema import ClipComposerConfig

        if not isinstance(clip_indices, list) or not clip_indices:
            return
        style_cfg = style_config if isinstance(style_config, ClipComposerConfig) else None
        self._set_studio_render_busy(True, f"Render Batch 0/{len(clip_indices)}...")
        self.studio_widget.batch_export_btn.setText(f"Batch 0/{len(clip_indices)}...")
        if hasattr(self, "step3_progress_bar"):
            self.step3_progress_bar.setValue(0)
            self.step3_progress_bar.setProperty("tone", "info")
            _repolish(self.step3_progress_bar)
        self._bridge.render_batch_custom_clips(
            job_id=job_id,
            clip_indices=[int(i) for i in clip_indices],
            style_template_config=style_cfg,
            force_redetect_faces=bool(force_redetect_faces),
        )

    def _on_studio_standalone_render_requested(self, source_video_path: str, config: object) -> None:
        from atro.core.composer_schema import ClipComposerConfig

        if isinstance(config, ClipComposerConfig):
            self._set_studio_render_busy(True, "Sedang Render FFmpeg...")
            self._bridge.render_standalone_clip(source_video_path, config)

    def _on_custom_clip_rendered(self, job_id: str, rendered: object) -> None:
        self._set_studio_render_busy(False)
        state = self._jobs.get(job_id)
        if state is not None and self._selected_job_id == job_id:
            self._refresh_detail_panel(state)
        out_path = Path(str(getattr(rendered, "output_path", ""))).name
        self._toasts.show_toast(
            "Export Selesai",
            f"Klip {out_path} berhasil dirender.",
            tone="ok",
            reduce_motion=self._settings.ui_reduce_motion,
        )

    def _on_studio_style_applied_all(self, count: int) -> None:
        self._toasts.show_toast(
            f"Gaya Diterapkan ke {count} Klip",
            "Buka 'Edit & Render Beberapa/Semua...' untuk merender ulang.",
            tone="info",
            reduce_motion=self._settings.ui_reduce_motion,
        )

    def _on_batch_render_progress(self, job_id: str, current_idx: int, total_cnt: int, clip_title: str) -> None:
        self.studio_widget.export_btn.setText(f"Render {current_idx}/{total_cnt}...")
        self.studio_widget.batch_export_btn.setText(f"Batch {current_idx}/{total_cnt}...")
        if self._selected_job_id == job_id:
            pct = int(round((current_idx / max(1, total_cnt)) * 100.0))
            self.step3_progress_bar.setValue(pct)
            self.detail_title.setText(
                f"Job {job_id}  •  Render Batch {current_idx}/{total_cnt}: {clip_title}"
            )
            state = self._jobs.get(job_id)
            if state is not None:
                self._refresh_detail_panel(state)

    def _on_batch_clips_rendered(self, job_id: str, rendered_obj: object) -> None:
        self._set_studio_render_busy(False)
        rendered_list = list(rendered_obj) if isinstance(rendered_obj, list) else []
        state = self._jobs.get(job_id)
        if state is not None and self._selected_job_id == job_id:
            self._refresh_detail_panel(state)
            if state.status == JobStatus.COMPLETED:
                self.step3_progress_bar.setValue(100)
                self.step3_progress_bar.setProperty("tone", "ok")
                _repolish(self.step3_progress_bar)
        self._toasts.show_toast(
            "Render Batch Selesai",
            f"{len(rendered_list)} klip berhasil diperbarui dan siap ditonton.",
            tone="ok",
            reduce_motion=self._settings.ui_reduce_motion,
        )

    def _on_alternative_clips_generated(self, job_id: str, new_count: int) -> None:
        self._set_studio_render_busy(False)
        state = self._jobs.get(job_id)
        if state is not None and self._selected_job_id == job_id:
            self._refresh_detail_panel(state)
        self._toasts.show_toast(
            "Klip Alternatif Siap",
            f"{new_count} klip alternatif baru telah ditambahkan.",
            tone="ok",
            reduce_motion=self._settings.ui_reduce_motion,
        )

    def _on_standalone_clip_rendered(self, rendered: object) -> None:
        self._set_studio_render_busy(False)
        out_path = Path(str(getattr(rendered, "output_path", ""))).name
        self._toasts.show_toast(
            "Export Studio Selesai",
            f"Video {out_path} berhasil diekspor.",
            tone="ok",
            reduce_motion=self._settings.ui_reduce_motion,
        )

    def _on_bridge_error(self, msg: str) -> None:
        self._set_studio_render_busy(False)
        headline, hint = humanize_error(msg)
        QMessageBox.critical(self, "Kendala Pemrosesan", f"{headline}\n\nSaran: {hint}")

    def selected_step3_clip_indices(self) -> list[int]:
        return [card.clip_index() for card in self._clip_cards if card.is_selected()]

    def _select_all_step3_clips(self, checked: bool) -> None:
        for card in self._clip_cards:
            card.select_chk.blockSignals(True)
            card.set_selected(checked)
            card.select_chk.blockSignals(False)
        self._update_selected_clips_ui()

    def _update_selected_clips_ui(self) -> None:
        if not hasattr(self, "selected_clips_pill"):
            return
        total = len(self._clip_cards)
        sel = len(self.selected_step3_clip_indices())
        self.selected_clips_pill.setText(f"{sel}/{total} klip dipilih" if total else "0 klip dipilih")
        has_clips = total > 0
        self.select_all_clips_btn.setEnabled(has_clips)
        self.deselect_all_clips_btn.setEnabled(has_clips)
        self.batch_studio_btn.setEnabled(has_clips)
        self.batch_rerender_btn.setEnabled(sel > 0 and self.studio_widget.export_btn.isEnabled())
        if hasattr(self, "floating_batch_bar"):
            self.floating_batch_bar.setVisible(has_clips)

    def _open_batch_in_studio(self) -> None:
        if not self._selected_job_id:
            return
        state = self._jobs.get(self._selected_job_id)
        if state is None:
            return
        indices = self.selected_step3_clip_indices() or [c.clip_index() for c in self._clip_cards]
        if not indices:
            return
        self.studio_widget.load_job_context(state, select_clip_index=indices[0])
        self.studio_widget.set_batch_preselection(indices)
        self.go_to_step(3)

    def _rerender_selected_step3_clips(self) -> None:
        if not self._selected_job_id:
            return
        indices = self.selected_step3_clip_indices()
        if not indices:
            QMessageBox.warning(self, "Belum Ada Klip Dipilih", "Centang minimal 1 klip terlebih dahulu.")
            return
        self._on_studio_batch_render_requested(
            self._selected_job_id,
            indices,
            None,
            True,
        )

    def _on_job_card_clicked(self, job_id: str) -> None:
        self._select_job(job_id)
        self.go_to_step(2)

    def _cancel_selected_job(self) -> None:
        if self._selected_job_id:
            self._bridge.cancel_job(self._selected_job_id)

    def _resume_selected_job(self) -> None:
        if self._selected_job_id:
            self._bridge.resume_job(self._selected_job_id)

    def _upsert_job(self, state: JobState, select: bool = False) -> None:
        self._jobs[state.job_id] = state
        self.jobs_empty_label.setVisible(False)
        if hasattr(self, "jobs_empty_state"):
            self.jobs_empty_state.setVisible(False)
        self.job_count_pill.setText(str(len(self._jobs)))

        card = self._job_cards.get(state.job_id)
        if card is None:
            card = JobCard(state)
            card.clicked.connect(self._on_job_card_clicked)
            card.open_folder_requested.connect(self._open_job_folder)
            card.resume_requested.connect(self._bridge.resume_job)
            card.cancel_requested.connect(self._bridge.cancel_job)
            self._job_cards[state.job_id] = card
            self.jobs_vbox.insertWidget(0, card)
        else:
            card.update_state(state)

        if select or self._selected_job_id is None:
            self._select_job(state.job_id)
        elif self._selected_job_id == state.job_id and state.status == JobStatus.COMPLETED:
            self.studio_widget.load_job_context(state)

    def _select_job(self, job_id: str) -> None:
        self._selected_job_id = job_id
        for jid, card in self._job_cards.items():
            card.set_selected(jid == job_id)

        state = self._jobs.get(job_id)
        if state is not None:
            self._refresh_detail_panel(state)
            self.log_viewer.load_file(Path(state.log_path))
            self.studio_widget.load_job_context(state)

    def _update_stage_nodes(self, state: JobState) -> None:
        order = {
            JobStatus.QUEUED: -1,
            JobStatus.DOWNLOADING: 0,
            JobStatus.TRANSCRIBING: 1,
            JobStatus.ANALYZING: 2,
            JobStatus.CUTTING: 3,
            JobStatus.COMPLETED: 4,
        }
        cur_idx = order.get(state.status, -1)
        for idx, node in enumerate(self.stage_nodes):
            if state.status == JobStatus.COMPLETED or idx < cur_idx:
                st = "done"
            elif idx == cur_idx:
                st = "active"
            else:
                st = "idle"
            node.setProperty("state", st)
            _repolish(node)

        self.step3_progress_bar.setValue(int(round(state.progress_pct)))
        if state.status == JobStatus.COMPLETED:
            tone = "ok"
        elif state.status == JobStatus.FAILED:
            tone = "error"
        elif state.status == JobStatus.CANCELLED:
            tone = "warn"
        else:
            tone = "info"
        self.step3_progress_bar.setProperty("tone", tone)
        _repolish(self.step3_progress_bar)

        is_active = state.status in (
            JobStatus.QUEUED,
            JobStatus.DOWNLOADING,
            JobStatus.TRANSCRIBING,
            JobStatus.ANALYZING,
            JobStatus.CUTTING,
        )
        can_resume = state.status in (JobStatus.FAILED, JobStatus.CANCELLED)
        self.step3_cancel_btn.setVisible(is_active)
        self.step3_resume_btn.setVisible(can_resume)

    def _refresh_detail_panel(self, state: JobState) -> None:
        self.detail_title.setText(
            f"Job {state.job_id}  •  {state.request.display_source}  •  {state.status.value} ({state.progress_pct:.0f}%)"
        )
        self._update_stage_nodes(state)

        if state.error:
            headline, hint = humanize_error(state.error)
            self.job_error_label.setText(f"{headline}\nSaran: {hint}")
            self.job_error_banner.setVisible(True)
        else:
            self.job_error_banner.setVisible(False)

        clips_dir = Path(state.workspace_dir) / "clips"
        self.open_clips_dir_btn.setVisible(clips_dir.is_dir())

        prev_checked = (
            {c.clip_index(): c.is_selected() for c in self._clip_cards}
            if getattr(self, "_last_refreshed_job_id", None) == state.job_id
            else {}
        )
        self._last_refreshed_job_id = state.job_id
        self._clip_cards.clear()

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
                card = ClipCard(clip)
                if clip.clip_index in prev_checked:
                    card.set_selected(prev_checked[clip.clip_index])
                card.customize_requested.connect(self._open_clip_in_studio)
                card.publish_kit_requested.connect(self._export_clip_publish_kit)
                card.selection_changed.connect(lambda _i, _c: self._update_selected_clips_ui())
                self._clip_cards.append(card)
                self.clips_vbox.addWidget(card)
        else:
            if state.status == JobStatus.COMPLETED:
                self.clips_empty_label.setText("Job selesai namun tidak ada data klip yang termuat.")
            elif state.status == JobStatus.FAILED:
                self.clips_empty_label.setText("Job berhenti karena error. Klik 'Lihat Log Proses' atau 'Lanjutkan (Resume)'.")
            elif state.status == JobStatus.CANCELLED:
                self.clips_empty_label.setText("Job dibatalkan. Klik 'Lanjutkan (Resume)' jika ingin melanjutkan.")
            else:
                self.clips_empty_label.setText(
                    f"Sedang memproses tahap '{state.current_stage}' ({state.progress_pct:.0f}%)...\n"
                    "Hasil klip akan muncul di sini setelah tahap render selesai."
                )
            self.clips_empty_label.setVisible(True)

        self.clips_vbox.addStretch()
        self._update_selected_clips_ui()

        has_transcript = bool(
            state.artifacts.transcript_json and Path(state.artifacts.transcript_json).is_file()
        )
        if hasattr(self, "alt_clips_btn"):
            self.alt_clips_btn.setVisible(state.status == JobStatus.COMPLETED and has_transcript)

        if hasattr(self, "topic_outline_label"):
            outline_txt = ""
            an_path_str = state.artifacts.analysis_json
            if an_path_str and Path(an_path_str).is_file():
                try:
                    an_obj = AnalysisResult.model_validate_json(
                        Path(an_path_str).read_text(encoding="utf-8")
                    )
                    if an_obj.topic_outline:
                        topics_preview = "  •  ".join(
                            f"[{t.start_time:.0f}s] {t.topic}" for t in an_obj.topic_outline[:4]
                        )
                        outline_txt = f"Topik Pembahasan: {topics_preview}"
                except Exception:
                    outline_txt = ""
            self.topic_outline_label.setText(outline_txt)
            self.topic_outline_label.setVisible(bool(outline_txt))

    def _on_platform_preset_changed(self) -> None:
        plat = self.platform_combo.currentData() or "youtube_shorts"
        if plat == "youtube_shorts":
            self.aspect_combo.setCurrentText("9:16")
            if self.max_dur_spin.value() > 60:
                self.max_dur_spin.setValue(58)
            if self.min_dur_spin.value() > 45:
                self.min_dur_spin.setValue(20)
        else:
            self.aspect_combo.setCurrentText("9:16")
            if self.max_dur_spin.value() < 75:
                self.max_dur_spin.setValue(90)

    def _load_offline_demo_project(self) -> None:
        try:
            preflight = self._bridge.get_preflight()
            state = create_offline_demo_job(
                workspace_dir=self._settings.workspace_dir,
                platform_target=self.platform_combo.currentData() if hasattr(self, "platform_combo") else "youtube_shorts",
                ffmpeg_path=preflight.ffmpeg.path if preflight.ffmpeg.available else None,
            )
            self._upsert_job(state, select=True)
            self.go_to_step(2)
            self._toasts.show_toast(
                "Project Contoh Siap",
                "Video podcast demo 5 orang & 3 kamera siap dicoba di Studio Preview tanpa API Key!",
                tone="ok",
                reduce_motion=self._settings.ui_reduce_motion,
            )
        except Exception as exc:
            headline, hint = humanize_error(str(exc))
            QMessageBox.warning(self, headline, f"{headline}\n\n{hint}")

    def _export_clip_publish_kit(self, clip_index: int) -> None:
        if not self._selected_job_id:
            return
        state = self._jobs.get(self._selected_job_id)
        if state is None or not state.artifacts.render_result_json:
            return
        try:
            rr = RenderResult.model_validate_json(
                Path(state.artifacts.render_result_json).read_text(encoding="utf-8")
            )
        except Exception:
            return
        target_clip = next((c for c in rr.rendered_clips if c.clip_index == clip_index), None)
        if target_clip is None:
            return

        segs = []
        if state.artifacts.transcript_json and Path(state.artifacts.transcript_json).is_file():
            try:
                segs = TranscriptResult.model_validate_json(
                    Path(state.artifacts.transcript_json).read_text(encoding="utf-8")
                ).segments
            except Exception:
                segs = []

        kits_dir = Path(state.workspace_dir) / "publish_kits"
        out_zip = kits_dir / f"clip_{clip_index:02d}_publish_kit.zip"
        preset_id = (
            (state.request.initial_style_overrides or {}).get("ready_template_id")
            or state.request.edit_format
            or "podcast"
        )
        try:
            export_publish_kit_zip(
                clip=target_clip,
                output_zip_path=out_zip,
                platform_target=state.request.platform_target,
                preset_id=str(preset_id),
                transcript_segments=segs,
            )
            self._toasts.show_toast(
                "Publish Kit Diekspor",
                f"Paket ZIP (MP4 + Cover PNG + Caption + Hashtag) disimpan di {out_zip.name}",
                tone="ok",
                reduce_motion=self._settings.ui_reduce_motion,
            )
            if os.name == "nt" and kits_dir.is_dir():
                os.startfile(str(kits_dir))  # type: ignore[attr-defined]
        except Exception as exc:
            headline, hint = humanize_error(str(exc))
            QMessageBox.warning(self, headline, f"{headline}\n\n{hint}")

    def _generate_alternative_clips(self) -> None:
        if not self._selected_job_id:
            return
        state = self._jobs.get(self._selected_job_id)
        if state is None:
            return
        self._set_studio_render_busy(True, "AI sedang mencari klip alternatif baru...")
        self._toasts.show_toast(
            "Mencari Klip Alternatif",
            "Menghindari momen klip lama tanpa transcribe ulang...",
            tone="info",
            reduce_motion=self._settings.ui_reduce_motion,
        )
        self._bridge.generate_alternative_clips(state.job_id)

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
    """Create QApplication, apply WinUI 3 Fluent stylesheet, and start MainWindow."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    settings = load_settings()
    app.setStyleSheet(build_stylesheet(settings.ui_theme))

    window = MainWindow()
    window.show()
    return app.exec()
