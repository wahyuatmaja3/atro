"""Modular Template Composer, Full Customization Controls, and Live 9:16 Preview Studio."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontDatabase, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from atro.config import AppSettings
from atro.core.composer_schema import (
    ClipComposerConfig,
    CommentBubbleItem,
    TranscriptSegmentOverride,
)
from atro.core.presets import (
    CAPTION_RECIPES,
    READY_TEMPLATES,
    apply_caption_recipe,
    build_preset_config,
    build_ready_template_config,
    extract_transcript_overrides,
    generate_social_copywriting,
    get_presets_dir,
    load_custom_preset,
    load_or_create_job_clip_config,
    normalize_ready_template_id,
    save_custom_preset,
    save_job_clip_config,
)
from atro.core.schemas import (
    AnalysisResult,
    ClipCandidate,
    DownloadResult,
    JobState,
    TranscriptResult,
    TranscriptSegment,
)
from atro.modules.cutter.backsound import scan_backsound_tracks
from atro.modules.cutter.composer_renderer import detect_composer_shot_crops_sync
from atro.modules.cutter.service import apply_initial_style_overrides, apply_style_template_from_config
from atro.ui.widgets.preview_canvas import (
    FrameExtractorCache,
    LivePreviewCanvas9x16,
    SourceCropSelectorWidget,
)


class ColorSwatchButton(QPushButton):
    """Flat button displaying a color swatch and opening QColorDialog on click."""

    color_changed = pyqtSignal(str)

    def __init__(self, initial_hex: str = "#FFFFFF", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hex = initial_hex.upper()
        self.setObjectName("SmallAction")
        self.setMinimumWidth(96)
        self._apply_visual()
        self.clicked.connect(self._pick_color)

    def color_hex(self) -> str:
        return self._hex

    def set_color_hex(self, hex_str: str) -> None:
        self._hex = (hex_str or "#FFFFFF").strip().upper()
        self._apply_visual()

    def _apply_visual(self) -> None:
        col = QColor(self._hex)
        if not col.isValid():
            col = QColor("#FFFFFF")
        lum = (0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()) / 255.0
        txt_col = "#0F172A" if lum > 0.6 else "#F8FAFC"
        self.setText(self._hex[:9])
        self.setStyleSheet(
            f"background-color: {col.name(QColor.NameFormat.HexRgb)}; "
            f"color: {txt_col}; border: 1px solid #475569; border-radius: 6px; padding: 4px 8px; font-weight: 600;"
        )

    def _pick_color(self) -> None:
        init = QColor(self._hex)
        chosen = QColorDialog.getColor(
            init if init.isValid() else QColor("#FFFFFF"),
            self,
            "Pilih Warna",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if chosen.isValid():
            if chosen.alpha() < 255:
                self._hex = chosen.name(QColor.NameFormat.HexArgb).upper()
            else:
                self._hex = chosen.name(QColor.NameFormat.HexRgb).upper()
            self._apply_visual()
            self.color_changed.emit(self._hex)


def _wrap_scroll(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    return scroll


def _repolish_widget(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


class ReadyTemplateCard(QFrame):
    """Selectable WinUI 3 ready-made template card for 1-click full styling."""

    clicked = pyqtSignal(str)

    def __init__(
        self,
        template_id: str,
        badge_text: str,
        title: str,
        description: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.template_id = template_id
        self.format_id = template_id
        self.setObjectName("StyleCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(104)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(5)

        top = QHBoxLayout()
        badge = QLabel(badge_text)
        badge.setObjectName("VersionPill")
        self._active_lbl = QLabel("Dipilih")
        self._active_lbl.setObjectName("StatusPill")
        self._active_lbl.setProperty("tone", "info")
        self._active_lbl.setVisible(False)
        top.addWidget(badge)
        top.addStretch()
        top.addWidget(self._active_lbl)
        layout.addLayout(top)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #FFFBEB;")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(description)
        desc_lbl.setObjectName("MutedText")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl, 1)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._active_lbl.setVisible(selected)
        _repolish_widget(self)

    def mousePressEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.template_id)
        super().mousePressEvent(event)


class HookTextEdit(QPlainTextEdit):
    """Multi-line hook banner editor preserving Enter newlines with QLineEdit-style .text()/.setText()."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMaximumHeight(68)
        self.setTabChangesFocus(True)
        self.setPlaceholderText("Ketik teks hook (tekan Enter untuk baris baru)...")

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str) -> None:
        normalized = value or ""
        if self.toPlainText() != normalized:
            self.setPlainText(normalized)


class BatchClipRenderDialog(QDialog):
    """Dialog for selecting multiple or all existing clips to batch-edit and re-render."""

    def __init__(
        self,
        clips: list[ClipCandidate],
        preselected_indices: Optional[list[int]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit & Render Beberapa / Semua Klip")
        self.setMinimumWidth(560)
        self.setMinimumHeight(460)
        self.action_mode: str = "render"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        note = QLabel(
            "Video sudah melalui proses Transcribe & Kurasi AI. Pilih klip yang ingin "
            "diperbarui editannya lalu render ulang langsung via FFmpeg:"
        )
        note.setWordWrap(True)
        note.setObjectName("MutedText")
        layout.addWidget(note)

        sel_bar = QHBoxLayout()
        sel_all_btn = QPushButton("Pilih Semua")
        sel_all_btn.setObjectName("SmallAction")
        sel_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        sel_none_btn = QPushButton("Batal Pilih")
        sel_none_btn.setObjectName("SmallAction")
        sel_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        sel_bar.addWidget(sel_all_btn)
        sel_bar.addWidget(sel_none_btn)
        sel_bar.addStretch()
        layout.addLayout(sel_bar)

        self.clips_list = QListWidget()
        pre_set = set(preselected_indices) if preselected_indices is not None else None
        for idx, clip in enumerate(clips, start=1):
            label = f"#{idx:02d} — {clip.title} [{clip.start_time:.1f}s – {clip.end_time:.1f}s]"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = (idx in pre_set) if pre_set is not None else True
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.clips_list.addItem(item)
        layout.addWidget(self.clips_list, 1)

        self.apply_style_chk = QCheckBox(
            "Terapkan gaya/template & settingan Studio saat ini ke semua klip terpilih"
        )
        self.apply_style_chk.setChecked(True)
        layout.addWidget(self.apply_style_chk)

        self.redetect_face_chk = QCheckBox(
            "Deteksi ulang kamera multi-angle & fokus wajah pembicara aktif (Active Speaker)"
        )
        self.redetect_face_chk.setChecked(True)
        layout.addWidget(self.redetect_face_chk)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Batal")
        cancel_btn.setObjectName("SmallAction")
        cancel_btn.clicked.connect(self.reject)

        save_only_btn = QPushButton("Simpan Gaya Saja")
        save_only_btn.setObjectName("SmallAction")
        save_only_btn.clicked.connect(lambda: self._finish("save_only"))

        render_btn = QPushButton("Simpan & Render Klip Terpilih (FFmpeg)")
        render_btn.setObjectName("PrimaryButton")
        render_btn.clicked.connect(lambda: self._finish("render"))

        btns.addWidget(cancel_btn)
        btns.addStretch()
        btns.addWidget(save_only_btn)
        btns.addWidget(render_btn)
        layout.addLayout(btns)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for r in range(self.clips_list.count()):
            it = self.clips_list.item(r)
            if it is not None:
                it.setCheckState(state)

    def _finish(self, mode: str) -> None:
        if not self.selected_clip_indices():
            QMessageBox.warning(self, "Belum Ada Klip Dipilih", "Centang minimal 1 klip terlebih dahulu.")
            return
        self.action_mode = mode
        self.accept()

    def selected_clip_indices(self) -> list[int]:
        out: list[int] = []
        for r in range(self.clips_list.count()):
            it = self.clips_list.item(r)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                val = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(val, int):
                    out.append(val)
        return out

    def should_apply_style(self) -> bool:
        return self.apply_style_chk.isChecked()

    def should_redetect_faces(self) -> bool:
        return self.redetect_face_chk.isChecked()


class ClipStudioWidget(QWidget):
    """Full Interactive Template Composer, Customization Panel, and 9:16 Live Preview."""

    render_requested = pyqtSignal(str, object)  # (job_id, ClipComposerConfig)
    batch_render_requested = pyqtSignal(str, object, object, bool)  # (job_id, indices, style_cfg, redetect)
    standalone_render_requested = pyqtSignal(str, object)  # (local_video_path, ClipComposerConfig)
    _auto_detect_done = pyqtSignal(int, str, object)  # (request_id, source_path_str, list[ShotCropSegment])

    def __init__(
        self,
        settings_provider: Callable[[], AppSettings],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings_provider = settings_provider
        self._frame_cache = FrameExtractorCache()
        self._job_state: Optional[JobState] = None
        self._download: Optional[DownloadResult] = None
        self._analysis: Optional[AnalysisResult] = None
        self._transcript_segments: list[TranscriptSegment] = []
        self._current_clip_reason: str = ""
        self._source_path: Optional[Path] = None
        self._video_duration_sec: float = 60.0

        self._config: ClipComposerConfig = build_preset_config("podcast")
        self._rel_time_sec: float = 0.0
        self._is_playing: bool = False
        self._syncing_ui: bool = False
        self._detect_in_progress: bool = False
        self._detect_request_id: int = 0
        self._preselected_batch_indices: Optional[list[int]] = None

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(33)  # ~30 FPS live preview playback
        self._play_timer.timeout.connect(self._on_playback_tick)
        self._auto_detect_done.connect(self._on_auto_detect_finished)

        self._build_ui()
        self._load_config_into_ui(self._config)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(10)

        # 1. Top Toolbar: Source / Clip selector + 4 Template Presets + Save/Load + Export
        toolbar = QFrame()
        toolbar.setObjectName("Card")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 8, 12, 8)
        tb_layout.setSpacing(8)

        tb_layout.addWidget(QLabel("Klip:"))
        self.clip_selector_combo = QComboBox()
        self.clip_selector_combo.setMinimumWidth(170)
        self.clip_selector_combo.currentIndexChanged.connect(self._on_clip_selected)
        tb_layout.addWidget(self.clip_selector_combo)

        self.load_local_btn = QPushButton("Muat Video...")
        self.load_local_btn.setObjectName("SmallAction")
        self.load_local_btn.clicked.connect(self._browse_standalone_video)
        tb_layout.addWidget(self.load_local_btn)

        tb_layout.addSpacing(6)
        preset_lbl = QLabel("Gaya Cepat:")
        preset_lbl.setObjectName("FieldLabel")
        tb_layout.addWidget(preset_lbl)

        self.preset_buttons: dict[str, QPushButton] = {}
        for pid, label in (
            ("podcast", "Podcast"),
            ("gaming_split", "Gaming Split"),
            ("twitch_comment", "Komentar Stream"),
            ("subathon", "Subathon"),
        ):
            btn = QPushButton(label)
            btn.setObjectName("ModeSwitch")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c=False, p=pid: self.apply_preset(p))
            self.preset_buttons[pid] = btn
            tb_layout.addWidget(btn)

        self.save_preset_btn = QPushButton("Simpan Preset")
        self.save_preset_btn.setObjectName("SmallAction")
        self.save_preset_btn.clicked.connect(self._save_preset_dialog)
        self.load_preset_btn = QPushButton("Muat Preset")
        self.load_preset_btn.setObjectName("SmallAction")
        self.load_preset_btn.clicked.connect(self._load_preset_dialog)
        tb_layout.addWidget(self.save_preset_btn)
        tb_layout.addWidget(self.load_preset_btn)

        tb_layout.addStretch()

        self.apply_style_all_btn = QPushButton("Terapkan Gaya ke Semua")
        self.apply_style_all_btn.setObjectName("SmallAction")
        self.apply_style_all_btn.setToolTip(
            "Salin gaya template, subtitle, warna, overlay & audio klip ini ke seluruh klip dalam job"
        )
        self.apply_style_all_btn.clicked.connect(self._on_apply_style_all_clicked)
        tb_layout.addWidget(self.apply_style_all_btn)

        self.batch_export_btn = QPushButton("Edit & Render Beberapa/Semua...")
        self.batch_export_btn.setObjectName("SmallAction")
        self.batch_export_btn.setToolTip(
            "Pilih beberapa atau semua klip yang sudah ada untuk dirender ulang sekaligus tanpa transcribe ulang"
        )
        self.batch_export_btn.clicked.connect(lambda: self.open_batch_render_dialog())
        tb_layout.addWidget(self.batch_export_btn)

        self.export_btn = QPushButton("Simpan & Render Klip Ini")
        self.export_btn.setObjectName("PrimaryButton")
        self.export_btn.clicked.connect(self._on_export_clicked)
        tb_layout.addWidget(self.export_btn)
        root.addWidget(toolbar)

        # 2. Main 2-Column Studio Splitter (Left: Spacious Visual Preview + Timeline | Right: Easy Controls)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setHandleWidth(8)

        # Left Column: View Mode Switch + Preview + Crop + Timeline
        visual_panel = QFrame()
        visual_panel.setObjectName("Card")
        vis_layout = QVBoxLayout(visual_panel)
        vis_layout.setContentsMargins(12, 10, 12, 10)
        vis_layout.setSpacing(8)

        # View Mode Switcher Row
        view_bar = QHBoxLayout()
        view_bar.setSpacing(6)
        view_lbl = QLabel("Tampilan:")
        view_lbl.setObjectName("FieldLabel")
        view_bar.addWidget(view_lbl)

        self.view_both_btn = QPushButton("Lihat Keduanya")
        self.view_both_btn.setObjectName("ModeSwitch")
        self.view_both_btn.setCheckable(True)
        self.view_both_btn.setChecked(True)
        self.view_both_btn.clicked.connect(lambda: self._set_preview_view_mode("both"))

        self.view_preview_btn = QPushButton("Fokus Preview (9:16 Besar)")
        self.view_preview_btn.setObjectName("ModeSwitch")
        self.view_preview_btn.setCheckable(True)
        self.view_preview_btn.clicked.connect(lambda: self._set_preview_view_mode("preview"))

        self.view_crop_btn = QPushButton("Fokus Atur Kamera (16:9)")
        self.view_crop_btn.setObjectName("ModeSwitch")
        self.view_crop_btn.setCheckable(True)
        self.view_crop_btn.clicked.connect(lambda: self._set_preview_view_mode("crop"))

        self.auto_detect_btn = QPushButton("Deteksi Wajah Otomatis")
        self.auto_detect_btn.setObjectName("SmallAction")
        self.auto_detect_btn.clicked.connect(self._run_auto_detect_crops)

        self.preview_info_lbl = QLabel("1080×1920")
        self.preview_info_lbl.setObjectName("VersionPill")

        view_bar.addWidget(self.view_both_btn)
        view_bar.addWidget(self.view_preview_btn)
        view_bar.addWidget(self.view_crop_btn)
        view_bar.addStretch()
        view_bar.addWidget(self.auto_detect_btn)
        view_bar.addWidget(self.preview_info_lbl)
        vis_layout.addLayout(view_bar)

        # Dual Canvas Area (Crop Selector + Live 9:16 Preview)
        self.canvas_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.canvas_splitter.setHandleWidth(6)

        self.crop_wrap = QWidget()
        crop_wrap_layout = QVBoxLayout(self.crop_wrap)
        crop_wrap_layout.setContentsMargins(0, 0, 0, 0)
        crop_wrap_layout.setSpacing(4)
        crop_hint = QLabel("Geser kotak Orange (Wajah/Atas) & Cyan (Layar/Bawah) langsung di video:")
        crop_hint.setObjectName("MutedText")
        crop_hint.setWordWrap(True)
        self.crop_selector = SourceCropSelectorWidget()
        self.crop_selector.crop_changed.connect(self._on_crop_dragged)
        crop_wrap_layout.addWidget(crop_hint)
        crop_wrap_layout.addWidget(self.crop_selector, 1)

        self.preview_wrap = QWidget()
        prev_wrap_layout = QVBoxLayout(self.preview_wrap)
        prev_wrap_layout.setContentsMargins(0, 0, 0, 0)
        prev_wrap_layout.setSpacing(4)
        prev_hint = QLabel("Hasil Preview (geser langsung subtitle / hook / komentar di layar):")
        prev_hint.setObjectName("MutedText")
        prev_hint.setWordWrap(True)
        self.preview_canvas = LivePreviewCanvas9x16()
        self.preview_canvas.overlay_moved.connect(self._on_preview_overlay_dragged)
        prev_wrap_layout.addWidget(prev_hint)
        prev_wrap_layout.addWidget(self.preview_canvas, 1)

        self.canvas_splitter.addWidget(self.crop_wrap)
        self.canvas_splitter.addWidget(self.preview_wrap)
        self.canvas_splitter.setStretchFactor(0, 4)
        self.canvas_splitter.setStretchFactor(1, 6)
        vis_layout.addWidget(self.canvas_splitter, 1)

        # Transport & Timeline Scrubber
        transport = QHBoxLayout()
        transport.setSpacing(10)
        self.play_btn = QPushButton("Putar Preview")
        self.play_btn.setObjectName("PrimaryButton")
        self.play_btn.setMinimumWidth(120)
        self.play_btn.clicked.connect(self._toggle_playback)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setRange(0, 1000)
        self.timeline_slider.valueChanged.connect(self._on_slider_scrubbed)

        self.time_readout_lbl = QLabel("00.00s / 30.00s")
        self.time_readout_lbl.setObjectName("VersionPill")
        transport.addWidget(self.play_btn)
        transport.addWidget(self.timeline_slider, 1)
        transport.addWidget(self.time_readout_lbl)
        vis_layout.addLayout(transport)

        trim_row = QHBoxLayout()
        trim_row.setSpacing(10)
        self.trim_start_spin = QDoubleSpinBox()
        self.trim_start_spin.setRange(0.0, 36000.0)
        self.trim_start_spin.setDecimals(2)
        self.trim_start_spin.setSingleStep(0.25)
        self.trim_start_spin.setSuffix(" dtk")
        self.trim_start_spin.valueChanged.connect(self._on_trim_changed)

        self.trim_end_spin = QDoubleSpinBox()
        self.trim_end_spin.setRange(0.5, 36000.0)
        self.trim_end_spin.setDecimals(2)
        self.trim_end_spin.setSingleStep(0.25)
        self.trim_end_spin.setSuffix(" dtk")
        self.trim_end_spin.valueChanged.connect(self._on_trim_changed)

        self.trim_pad_spin = QDoubleSpinBox()
        self.trim_pad_spin.setRange(0.0, 2.0)
        self.trim_pad_spin.setDecimals(2)
        self.trim_pad_spin.setSingleStep(0.05)
        self.trim_pad_spin.setSuffix(" dtk")
        self.trim_pad_spin.valueChanged.connect(self._on_trim_changed)

        for lbl_txt, w in (
            ("Mulai Detik:", self.trim_start_spin),
            ("Selesai Detik:", self.trim_end_spin),
            ("Jeda Halus (Pad):", self.trim_pad_spin),
        ):
            lbl = QLabel(lbl_txt)
            lbl.setObjectName("FieldLabel")
            trim_row.addWidget(lbl)
            trim_row.addWidget(w)
        trim_row.addStretch()
        vis_layout.addLayout(trim_row)

        # Right Column: Clear, Beginner-Friendly 5-Tab Settings Panel
        controls_panel = QFrame()
        controls_panel.setObjectName("Card")
        ctrl_layout = QVBoxLayout(controls_panel)
        ctrl_layout.setContentsMargins(10, 10, 10, 10)
        ctrl_layout.setSpacing(6)

        ctrl_hdr = QLabel("Pengaturan Tampilan & Audio Klip")
        ctrl_hdr.setObjectName("SectionTitle")
        ctrl_layout.addWidget(ctrl_hdr)

        self.ctrl_tabs = QTabWidget()
        self.ctrl_tabs.addTab(_wrap_scroll(self._build_template_tab()), "1. Template")
        self.ctrl_tabs.addTab(_wrap_scroll(self._build_layout_tab()), "2. Tata Letak")
        self.ctrl_tabs.addTab(_wrap_scroll(self._build_subtitle_tab()), "3. Teks Subtitle")
        self.ctrl_tabs.addTab(_wrap_scroll(self._build_overlay_tab()), "4. Judul & Komentar")
        self.ctrl_tabs.addTab(_wrap_scroll(self._build_audio_tab()), "5. Musik & Suara")
        self.ctrl_tabs.addTab(self._build_transcript_tab(), "6. Edit Kata")
        ctrl_layout.addWidget(self.ctrl_tabs, 1)

        main_splitter.addWidget(visual_panel)
        main_splitter.addWidget(controls_panel)
        main_splitter.setStretchFactor(0, 56)
        main_splitter.setStretchFactor(1, 44)
        root.addWidget(main_splitter, 1)

    def _set_preview_view_mode(self, mode: str) -> None:
        self.view_both_btn.setChecked(mode == "both")
        self.view_preview_btn.setChecked(mode == "preview")
        self.view_crop_btn.setChecked(mode == "crop")
        self.crop_wrap.setVisible(mode in ("both", "crop"))
        self.preview_wrap.setVisible(mode in ("both", "preview"))

    def _build_template_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setSpacing(10)

        info_card = QFrame()
        info_card.setObjectName("HeroCard")
        info_l = QVBoxLayout(info_card)
        info_l.setContentsMargins(12, 10, 12, 10)
        info_l.setSpacing(4)
        info_title = QLabel("Pilih Template Jadi Sekali Klik")
        info_title.setObjectName("SectionTitle")
        info_desc = QLabel(
            "Klik salah satu Template Jadi di bawah untuk langsung menerapkan Tata Letak, "
            "Teks Subtitle, Judul/Komentar, dan Musik sekaligus tanpa perlu atur manual satu per satu. "
            "(Jika ingin kustomisasi detail, buka tab 2–6)."
        )
        info_desc.setObjectName("MutedText")
        info_desc.setWordWrap(True)
        info_l.addWidget(info_title)
        info_l.addWidget(info_desc)
        vbox.addWidget(info_card)

        self.template_cards: dict[str, ReadyTemplateCard] = {}
        grid = QGridLayout()
        grid.setSpacing(8)
        for idx, (tid, meta) in enumerate(READY_TEMPLATES.items()):
            card = ReadyTemplateCard(
                template_id=tid,
                badge_text=str(meta["badge"]),
                title=str(meta["title"]),
                description=str(meta["description"]),
            )
            card.clicked.connect(self.apply_preset)
            self.template_cards[tid] = card
            grid.addWidget(card, idx // 2, idx % 2)
        vbox.addLayout(grid)

        custom_grp = QGroupBox("Template Kustom Anda (Simpan / Muat JSON)")
        cg_l = QVBoxLayout(custom_grp)
        cg_l.setSpacing(6)
        btn_row = QHBoxLayout()
        save_tpl_btn = QPushButton("Simpan Jadi Template Baru...")
        save_tpl_btn.setObjectName("SmallAction")
        save_tpl_btn.clicked.connect(self._save_preset_dialog)
        load_tpl_btn = QPushButton("Muat File Template (.json)...")
        load_tpl_btn.setObjectName("SmallAction")
        load_tpl_btn.clicked.connect(self._load_preset_dialog)
        refresh_tpl_btn = QPushButton("Segarkan Daftar")
        refresh_tpl_btn.setObjectName("SmallAction")
        refresh_tpl_btn.clicked.connect(self._refresh_saved_templates_list)
        btn_row.addWidget(save_tpl_btn)
        btn_row.addWidget(load_tpl_btn)
        btn_row.addWidget(refresh_tpl_btn)
        btn_row.addStretch()
        cg_l.addLayout(btn_row)

        self.saved_templates_list = QListWidget()
        self.saved_templates_list.setMaximumHeight(120)
        self.saved_templates_list.itemClicked.connect(self._on_saved_template_clicked)
        cg_l.addWidget(self.saved_templates_list)
        self._refresh_saved_templates_list()

        vbox.addWidget(custom_grp)
        vbox.addStretch()
        return w

    def _build_layout_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setSpacing(10)

        mode_grp = QGroupBox("Model Tampilan Layar")
        mf = QFormLayout(mode_grp)
        mf.setSpacing(8)

        self.layout_mode_combo = QComboBox()
        self.layout_mode_combo.addItem("Fokus 1 Orang Penuh (Podcast)", "single_focus")
        self.layout_mode_combo.addItem("Bagi Atas-Bawah (Gaming / Reaksi)", "split_screen")
        self.layout_mode_combo.addItem("Wajah Mengambang / PiP Facecam (Gameplay Penuh)", "pip_facecam")
        self.layout_mode_combo.addItem("Bingkai Tengah + Blur (Streamer)", "blurred_frame")
        self.layout_mode_combo.addItem("Bagi 2 Pembicara (Multi-Speaker)", "multi_speaker_split")
        self.layout_mode_combo.currentIndexChanged.connect(self._on_ui_mutated)
        mf.addRow("Jenis Tata Letak", self.layout_mode_combo)

        split_row = QHBoxLayout()
        self.split_ratio_slider = QSlider(Qt.Orientation.Horizontal)
        self.split_ratio_slider.setRange(20, 80)
        self.split_ratio_lbl = QLabel("40:60")
        self.split_ratio_slider.valueChanged.connect(self._on_ui_mutated)
        split_row.addWidget(self.split_ratio_slider, 1)
        split_row.addWidget(self.split_ratio_lbl)
        mf.addRow("Ukuran Atas vs Bawah", split_row)
        vbox.addWidget(mode_grp)

        div_grp = QGroupBox("Garis Pembatas Tengah (Lurus / Miring)")
        df = QFormLayout(div_grp)
        df.setSpacing(8)
        self.divider_enabled_chk = QCheckBox("Tampilkan Garis Pembatas Tengah")
        self.divider_enabled_chk.toggled.connect(self._on_ui_mutated)
        df.addRow(self.divider_enabled_chk)

        self.divider_thick_spin = QSpinBox()
        self.divider_thick_spin.setRange(0, 40)
        self.divider_thick_spin.setSuffix(" px")
        self.divider_thick_spin.valueChanged.connect(self._on_ui_mutated)
        df.addRow("Ketebalan Garis", self.divider_thick_spin)

        div_col_row = QHBoxLayout()
        self.divider_color_btn = ColorSwatchButton("#F97316")
        self.divider_color_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.divider_glow_chk = QCheckBox("Efek Cahaya (Glow)")
        self.divider_glow_chk.toggled.connect(self._on_ui_mutated)
        div_col_row.addWidget(self.divider_color_btn)
        div_col_row.addWidget(self.divider_glow_chk)
        df.addRow("Warna Garis", div_col_row)

        ang_row = QHBoxLayout()
        self.divider_angle_slider = QSlider(Qt.Orientation.Horizontal)
        self.divider_angle_slider.setRange(-25, 25)
        self.divider_angle_lbl = QLabel("0°")
        self.divider_angle_slider.valueChanged.connect(self._on_ui_mutated)
        ang_row.addWidget(self.divider_angle_slider, 1)
        ang_row.addWidget(self.divider_angle_lbl)
        df.addRow("Kemiringan (Ala Subathon)", ang_row)
        vbox.addWidget(div_grp)

        blur_grp = QGroupBox("Pengaturan Bingkai Blur (Mode Streamer)")
        bf = QFormLayout(blur_grp)
        bf.setSpacing(8)
        self.blur_aspect_combo = QComboBox()
        self.blur_aspect_combo.addItems(["4:3", "1:1", "16:9"])
        self.blur_aspect_combo.currentIndexChanged.connect(self._on_ui_mutated)
        bf.addRow("Bentuk Kotak Video Tengah", self.blur_aspect_combo)

        blur_row = QHBoxLayout()
        self.blur_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.blur_strength_slider.setRange(2, 60)
        self.blur_strength_lbl = QLabel("24")
        self.blur_strength_slider.valueChanged.connect(self._on_ui_mutated)
        blur_row.addWidget(self.blur_strength_slider, 1)
        blur_row.addWidget(self.blur_strength_lbl)
        bf.addRow("Tingkat Blur Belakang", blur_row)
        vbox.addWidget(blur_grp)

        pip_grp = QGroupBox("Bingkai Sudut, Neon & PiP Facecam")
        pf = QFormLayout(pip_grp)
        pf.setSpacing(8)
        pip_shape_row = QHBoxLayout()
        self.pip_shape_combo = QComboBox()
        self.pip_shape_combo.addItem("Lingkaran (Circle)", "circle")
        self.pip_shape_combo.addItem("Kotak Membulat", "rounded_rect")
        self.pip_shape_combo.currentIndexChanged.connect(self._on_ui_mutated)
        self.pip_pos_combo = QComboBox()
        self.pip_pos_combo.addItem("Kanan Atas", "top_right")
        self.pip_pos_combo.addItem("Kiri Atas", "top_left")
        self.pip_pos_combo.addItem("Kanan Bawah", "bottom_right")
        self.pip_pos_combo.addItem("Kiri Bawah", "bottom_left")
        self.pip_pos_combo.currentIndexChanged.connect(self._on_ui_mutated)
        pip_shape_row.addWidget(self.pip_shape_combo)
        pip_shape_row.addWidget(self.pip_pos_combo)
        pf.addRow("Bentuk & Sudut PiP", pip_shape_row)

        pip_scale_row = QHBoxLayout()
        self.pip_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.pip_scale_slider.setRange(18, 55)
        self.pip_scale_lbl = QLabel("34%")
        self.pip_scale_slider.valueChanged.connect(self._on_ui_mutated)
        pip_scale_row.addWidget(self.pip_scale_slider, 1)
        pip_scale_row.addWidget(self.pip_scale_lbl)
        pf.addRow("Ukuran Facecam PiP", pip_scale_row)

        frame_row = QHBoxLayout()
        self.frame_corner_spin = QSpinBox()
        self.frame_corner_spin.setRange(0, 120)
        self.frame_corner_spin.setSuffix(" px")
        self.frame_corner_spin.valueChanged.connect(self._on_ui_mutated)
        self.frame_border_w_spin = QSpinBox()
        self.frame_border_w_spin.setRange(0, 24)
        self.frame_border_w_spin.setSuffix(" px")
        self.frame_border_w_spin.valueChanged.connect(self._on_ui_mutated)
        self.frame_border_col_btn = ColorSwatchButton("#F97316")
        self.frame_border_col_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        frame_row.addWidget(QLabel("Radius"))
        frame_row.addWidget(self.frame_corner_spin)
        frame_row.addWidget(QLabel("Garis"))
        frame_row.addWidget(self.frame_border_w_spin)
        frame_row.addWidget(self.frame_border_col_btn)
        pf.addRow("Sudut & Garis Bingkai", frame_row)
        vbox.addWidget(pip_grp)

        # Collapsible numeric coordinates for power users (hidden by default for beginners)
        self.manual_crop_coords_chk = QCheckBox("Tampilkan Angka Koordinat Crop Manual (X, Y, Lebar, Tinggi)")
        self.manual_crop_coords_chk.setChecked(False)
        vbox.addWidget(self.manual_crop_coords_chk)

        self.manual_crop_widget = QGroupBox("Koordinat Crop Halus (0.00 - 1.00)")
        self.manual_crop_widget.setVisible(False)
        self.manual_crop_coords_chk.toggled.connect(self.manual_crop_widget.setVisible)
        cf = QFormLayout(self.manual_crop_widget)

        def _make_crop_spins() -> tuple[QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox]:
            spins = []
            for lo, hi in ((0.0, 0.95), (0.0, 0.95), (0.05, 1.0), (0.05, 1.0)):
                sp = QDoubleSpinBox()
                sp.setRange(lo, hi)
                sp.setSingleStep(0.02)
                sp.setDecimals(2)
                sp.valueChanged.connect(self._on_crop_spin_changed)
                spins.append(sp)
            return spins[0], spins[1], spins[2], spins[3]

        self.top_x, self.top_y, self.top_w, self.top_h = _make_crop_spins()
        top_grid = QHBoxLayout()
        for sp in (self.top_x, self.top_y, self.top_w, self.top_h):
            top_grid.addWidget(sp)
        cf.addRow("Kotak Atas (X,Y,W,H)", top_grid)

        self.bot_x, self.bot_y, self.bot_w, self.bot_h = _make_crop_spins()
        bot_grid = QHBoxLayout()
        for sp in (self.bot_x, self.bot_y, self.bot_w, self.bot_h):
            bot_grid.addWidget(sp)
        cf.addRow("Kotak Bawah (X,Y,W,H)", bot_grid)
        vbox.addWidget(self.manual_crop_widget)
        vbox.addStretch()
        return w

    def _build_subtitle_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setSpacing(10)

        basic_grp = QGroupBox("Teks & Sinkronisasi Subtitle")
        form = QFormLayout(basic_grp)
        form.setSpacing(8)

        self.sub_enabled_chk = QCheckBox("Tampilkan Teks Subtitle Otomatis")
        self.sub_enabled_chk.toggled.connect(self._on_ui_mutated)
        form.addRow(self.sub_enabled_chk)

        self.recipe_combo = QComboBox()
        for rid, meta in CAPTION_RECIPES.items():
            self.recipe_combo.addItem(meta["label"], rid)
        self.recipe_combo.activated.connect(self._on_caption_recipe_selected)
        form.addRow("Resep Gaya Viral (1-Klik)", self.recipe_combo)

        self.sub_style_combo = QComboBox()
        self.sub_style_combo.addItem("Sorot Per Kata (Karaoke)", "karaoke_highlight")
        self.sub_style_combo.addItem("Cepat 1-2 Kata (Ala Streamer)", "kinetic_1_2")
        self.sub_style_combo.addItem("Kalimat 3-4 Kata (Ala Podcast)", "podcast_3_4")
        self.sub_style_combo.addItem("Teks Polos Tanpa Sorotan", "plain")
        self.sub_style_combo.currentIndexChanged.connect(self._on_ui_mutated)
        form.addRow("Gaya Tampilan Teks", self.sub_style_combo)

        self.sub_offset_spin = QDoubleSpinBox()
        self.sub_offset_spin.setRange(-1.50, 1.50)
        self.sub_offset_spin.setSingleStep(0.05)
        self.sub_offset_spin.setDecimals(2)
        self.sub_offset_spin.setSuffix(" dtk")
        self.sub_offset_spin.setToolTip("Isi angka + (misal +0.15) jika teks muncul terlalu cepat, atau - jika terlambat")
        self.sub_offset_spin.valueChanged.connect(self._on_ui_mutated)
        form.addRow("Geser Waktu Teks (+Tunda / -Maju)", self.sub_offset_spin)

        font_row = QHBoxLayout()
        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        defaults = ["Arial Black", "Impact", "Segoe UI", "Montserrat", "Tahoma", "Verdana"]
        try:
            families = sorted(set(defaults + list(QFontDatabase.families())))
        except Exception:
            families = defaults
        self.font_combo.addItems(families)
        self.font_combo.currentTextChanged.connect(lambda _t: self._on_ui_mutated())
        self.font_browse_btn = QPushButton("Pilih File Font...")
        self.font_browse_btn.setObjectName("SmallAction")
        self.font_browse_btn.clicked.connect(self._browse_font_file)
        font_row.addWidget(self.font_combo, 1)
        font_row.addWidget(self.font_browse_btn)
        form.addRow("Jenis Huruf (Font)", font_row)

        self.custom_font_lbl = QLabel("Font bawaan sistem")
        self.custom_font_lbl.setObjectName("MutedText")
        form.addRow("File Font Lokal", self.custom_font_lbl)

        self.sub_size_spin = QSpinBox()
        self.sub_size_spin.setRange(20, 140)
        self.sub_size_spin.setSuffix(" pt")
        self.sub_size_spin.valueChanged.connect(self._on_ui_mutated)
        form.addRow("Ukuran Huruf", self.sub_size_spin)

        self.sub_place_combo = QComboBox()
        self.sub_place_combo.addItem("Di Garis Tengah Split", "split_line_center")
        self.sub_place_combo.addItem("Di Bagian Bawah Layar", "bottom_safe")
        self.sub_place_combo.addItem("Di Bagian Atas Layar", "top_safe")
        self.sub_place_combo.addItem("Geser Bebas (Custom X/Y)", "custom")
        self.sub_place_combo.currentIndexChanged.connect(self._on_placement_preset_changed)
        form.addRow("Posisi Subtitle", self.sub_place_combo)

        pos_row = QHBoxLayout()
        self.sub_pos_x_spin = QSpinBox()
        self.sub_pos_x_spin.setRange(60, 1020)
        self.sub_pos_x_spin.valueChanged.connect(self._on_ui_mutated)
        self.sub_pos_y_spin = QSpinBox()
        self.sub_pos_y_spin.setRange(120, 1840)
        self.sub_pos_y_spin.valueChanged.connect(self._on_ui_mutated)
        pos_row.addWidget(QLabel("Kiri-Kanan (X)"))
        pos_row.addWidget(self.sub_pos_x_spin)
        pos_row.addWidget(QLabel("Atas-Bawah (Y)"))
        pos_row.addWidget(self.sub_pos_y_spin)
        form.addRow("Koordinat Teks", pos_row)
        vbox.addWidget(basic_grp)

        style_grp = QGroupBox("Warna, Garis Tepi & Animasi")
        sf = QFormLayout(style_grp)
        sf.setSpacing(8)

        col_row = QHBoxLayout()
        self.sub_primary_btn = ColorSwatchButton("#FFFFFF")
        self.sub_primary_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.sub_highlight_btn = ColorSwatchButton("#FACC15")
        self.sub_highlight_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.sub_outline_btn = ColorSwatchButton("#000000")
        self.sub_outline_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        col_row.addWidget(self.sub_primary_btn)
        col_row.addWidget(self.sub_highlight_btn)
        col_row.addWidget(self.sub_outline_btn)
        sf.addRow("Warna Utama / Kata Aktif / Tepi", col_row)

        stroke_row = QHBoxLayout()
        self.sub_outline_w_spin = QDoubleSpinBox()
        self.sub_outline_w_spin.setRange(0.0, 20.0)
        self.sub_outline_w_spin.setSingleStep(0.5)
        self.sub_outline_w_spin.valueChanged.connect(self._on_ui_mutated)
        self.sub_shadow_spin = QDoubleSpinBox()
        self.sub_shadow_spin.setRange(0.0, 15.0)
        self.sub_shadow_spin.setSingleStep(0.5)
        self.sub_shadow_spin.valueChanged.connect(self._on_ui_mutated)
        self.sub_glow_spin = QDoubleSpinBox()
        self.sub_glow_spin.setRange(0.0, 15.0)
        self.sub_glow_spin.setSingleStep(0.5)
        self.sub_glow_spin.valueChanged.connect(self._on_ui_mutated)
        stroke_row.addWidget(QLabel("Tebal Tepi"))
        stroke_row.addWidget(self.sub_outline_w_spin)
        stroke_row.addWidget(QLabel("Bayangan"))
        stroke_row.addWidget(self.sub_shadow_spin)
        stroke_row.addWidget(QLabel("Glow"))
        stroke_row.addWidget(self.sub_glow_spin)
        sf.addRow("Ketebalan & Efek", stroke_row)

        anim_row = QHBoxLayout()
        self.sub_anim_combo = QComboBox()
        self.sub_anim_combo.addItems(["pop", "scale", "karaoke", "none"])
        self.sub_anim_combo.currentIndexChanged.connect(self._on_ui_mutated)
        self.sub_upper_chk = QCheckBox("HURUF BESAR SEMUA")
        self.sub_upper_chk.toggled.connect(self._on_ui_mutated)
        self.sub_max_words_spin = QSpinBox()
        self.sub_max_words_spin.setRange(1, 8)
        self.sub_max_words_spin.valueChanged.connect(self._on_ui_mutated)
        anim_row.addWidget(self.sub_anim_combo)
        anim_row.addWidget(self.sub_upper_chk)
        anim_row.addWidget(QLabel("Max Kata/Baris"))
        anim_row.addWidget(self.sub_max_words_spin)
        sf.addRow("Gerakan & Baris", anim_row)

        pill_row = QHBoxLayout()
        self.sub_bg_mode_combo = QComboBox()
        self.sub_bg_mode_combo.addItem("Tanpa Kotak Latar", "none")
        self.sub_bg_mode_combo.addItem("Kapsul Kata Aktif (MrBeast Pill)", "active_word_pill")
        self.sub_bg_mode_combo.addItem("Bar Kaca 1 Baris (Mageran Glass)", "full_line_box")
        self.sub_bg_mode_combo.currentIndexChanged.connect(self._on_ui_mutated)
        self.sub_pill_col_btn = ColorSwatchButton("#F97316")
        self.sub_pill_col_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.sub_pill_radius_spin = QSpinBox()
        self.sub_pill_radius_spin.setRange(0, 48)
        self.sub_pill_radius_spin.setSuffix(" px")
        self.sub_pill_radius_spin.valueChanged.connect(self._on_ui_mutated)
        pill_row.addWidget(self.sub_bg_mode_combo, 1)
        pill_row.addWidget(self.sub_pill_col_btn)
        pill_row.addWidget(self.sub_pill_radius_spin)
        sf.addRow("Latar Kapsul / Kotak", pill_row)

        viral_fx_row = QHBoxLayout()
        self.sub_dual_kw_chk = QCheckBox("Dual Keyword (Hormozi)")
        self.sub_dual_kw_chk.toggled.connect(self._on_ui_mutated)
        self.sub_sec_col_btn = ColorSwatchButton("#22C55E")
        self.sub_sec_col_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.sub_auto_emoji_chk = QCheckBox("Auto Emoji 🔥")
        self.sub_auto_emoji_chk.toggled.connect(self._on_ui_mutated)
        viral_fx_row.addWidget(self.sub_dual_kw_chk)
        viral_fx_row.addWidget(self.sub_sec_col_btn)
        viral_fx_row.addWidget(self.sub_auto_emoji_chk)
        sf.addRow("Efek Teks Kekinian", viral_fx_row)

        spk_row = QHBoxLayout()
        self.multi_speaker_chk = QCheckBox("Bedakan Warna Pembicara")
        self.multi_speaker_chk.toggled.connect(self._on_ui_mutated)
        self.spk2_hl_btn = ColorSwatchButton("#38BDF8")
        self.spk2_hl_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.action_col_btn = ColorSwatchButton("#F97316")
        self.action_col_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        spk_row.addWidget(self.multi_speaker_chk)
        spk_row.addWidget(QLabel("Pembicara 2:"))
        spk_row.addWidget(self.spk2_hl_btn)
        spk_row.addWidget(QLabel("Kata Aksi:"))
        spk_row.addWidget(self.action_col_btn)
        sf.addRow("Multi-Speaker & *LAUGHS*", spk_row)

        fn_row = QHBoxLayout()
        self.footnote_chk = QCheckBox("Tampilkan Catatan")
        self.footnote_chk.toggled.connect(self._on_ui_mutated)
        self.footnote_input = QLineEdit()
        self.footnote_input.setPlaceholderText("Catatan kecil di bawah subtitle (opsional)...")
        self.footnote_input.textChanged.connect(lambda _t: self._on_ui_mutated())
        fn_row.addWidget(self.footnote_chk)
        fn_row.addWidget(self.footnote_input, 1)
        sf.addRow("Catatan Tambahan", fn_row)
        vbox.addWidget(style_grp)
        vbox.addStretch()
        return w

    def _build_overlay_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setSpacing(10)

        # 1. Podcast Hook Banner
        hook_grp = QGroupBox("Podcast Hook Banner (Intro)")
        hf = QFormLayout(hook_grp)
        self.hook_enabled_chk = QCheckBox("Tampilkan Hook Banner")
        self.hook_enabled_chk.toggled.connect(self._on_ui_mutated)
        hf.addRow(self.hook_enabled_chk)

        self.hook_text_edit = HookTextEdit()
        self.hook_text_edit.textChanged.connect(self._on_ui_mutated)
        hf.addRow("Teks Hook", self.hook_text_edit)

        h_time_row = QHBoxLayout()
        self.hook_size_spin = QSpinBox()
        self.hook_size_spin.setRange(14, 100)
        self.hook_size_spin.setSuffix(" pt")
        self.hook_size_spin.valueChanged.connect(self._on_ui_mutated)
        self.hook_dur_spin = QDoubleSpinBox()
        self.hook_dur_spin.setRange(0.5, 12.0)
        self.hook_dur_spin.setSingleStep(0.2)
        self.hook_dur_spin.setSuffix(" s")
        self.hook_dur_spin.valueChanged.connect(self._on_ui_mutated)
        self.hook_y_spin = QSpinBox()
        self.hook_y_spin.setRange(60, 1750)
        self.hook_y_spin.valueChanged.connect(self._on_ui_mutated)
        h_time_row.addWidget(QLabel("Ukuran Font"))
        h_time_row.addWidget(self.hook_size_spin)
        h_time_row.addWidget(QLabel("Durasi"))
        h_time_row.addWidget(self.hook_dur_spin)
        h_time_row.addWidget(QLabel("Pos Y"))
        h_time_row.addWidget(self.hook_y_spin)
        hf.addRow("Font / Timing / Y", h_time_row)

        h_col_row = QHBoxLayout()
        self.hook_bg_btn = ColorSwatchButton("#E617110F")
        self.hook_bg_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.hook_txt_btn = ColorSwatchButton("#FFFFFF")
        self.hook_txt_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        self.hook_acc_btn = ColorSwatchButton("#FACC15")
        self.hook_acc_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        h_col_row.addWidget(self.hook_bg_btn)
        h_col_row.addWidget(self.hook_txt_btn)
        h_col_row.addWidget(self.hook_acc_btn)
        hf.addRow("BG / Teks / Aksen", h_col_row)

        h_tr_row = QHBoxLayout()
        self.hook_tr_in_combo = QComboBox()
        self.hook_tr_in_combo.addItems(["fade", "slide", "zoom", "whip", "none"])
        self.hook_tr_in_combo.currentIndexChanged.connect(self._on_ui_mutated)
        self.hook_tr_out_combo = QComboBox()
        self.hook_tr_out_combo.addItems(["fade", "slide", "zoom", "whip", "none"])
        self.hook_tr_out_combo.currentIndexChanged.connect(self._on_ui_mutated)
        h_tr_row.addWidget(QLabel("Masuk"))
        h_tr_row.addWidget(self.hook_tr_in_combo)
        h_tr_row.addWidget(QLabel("Keluar"))
        h_tr_row.addWidget(self.hook_tr_out_combo)
        hf.addRow("Transisi", h_tr_row)
        vbox.addWidget(hook_grp)

        # 2. Viewer Comment Bubbles (Twitch/Stream)
        cb_grp = QGroupBox("Viewer Comment Bubbles (Twitch/Stream)")
        cbf = QVBoxLayout(cb_grp)
        cb_top = QHBoxLayout()
        self.cb_list = QListWidget()
        self.cb_list.setMaximumHeight(90)
        self.cb_list.currentRowChanged.connect(self._on_bubble_selected)
        cb_btns = QVBoxLayout()
        self.cb_add_btn = QPushButton("+ Tambah")
        self.cb_add_btn.setObjectName("SmallAction")
        self.cb_add_btn.clicked.connect(self._add_comment_bubble)
        self.cb_del_btn = QPushButton("Hapus")
        self.cb_del_btn.setObjectName("SmallAction")
        self.cb_del_btn.clicked.connect(self._remove_comment_bubble)
        cb_btns.addWidget(self.cb_add_btn)
        cb_btns.addWidget(self.cb_del_btn)
        cb_btns.addStretch()
        cb_top.addWidget(self.cb_list, 1)
        cb_top.addLayout(cb_btns)
        cbf.addLayout(cb_top)

        cb_form = QFormLayout()
        self.cb_enabled_chk = QCheckBox("Bubble Aktif")
        self.cb_enabled_chk.toggled.connect(self._on_bubble_fields_changed)
        cb_form.addRow(self.cb_enabled_chk)

        cb_user_row = QHBoxLayout()
        self.cb_label_edit = QLineEdit("viewers:")
        self.cb_label_edit.textChanged.connect(lambda _t: self._on_bubble_fields_changed())
        self.cb_user_edit = QLineEdit("twitch_viewer")
        self.cb_user_edit.textChanged.connect(lambda _t: self._on_bubble_fields_changed())
        cb_user_row.addWidget(self.cb_label_edit)
        cb_user_row.addWidget(self.cb_user_edit)
        cb_form.addRow("Label / Nama", cb_user_row)

        self.cb_text_edit = QLineEdit()
        self.cb_text_edit.textChanged.connect(lambda _t: self._on_bubble_fields_changed())
        cb_form.addRow("Teks Komentar", self.cb_text_edit)

        cb_t_row = QHBoxLayout()
        self.cb_start_spin = QDoubleSpinBox()
        self.cb_start_spin.setRange(0.0, 600.0)
        self.cb_start_spin.setSingleStep(0.2)
        self.cb_start_spin.valueChanged.connect(self._on_bubble_fields_changed)
        self.cb_end_spin = QDoubleSpinBox()
        self.cb_end_spin.setRange(0.2, 600.0)
        self.cb_end_spin.setSingleStep(0.2)
        self.cb_end_spin.valueChanged.connect(self._on_bubble_fields_changed)
        self.cb_y_spin = QSpinBox()
        self.cb_y_spin.setRange(80, 1800)
        self.cb_y_spin.valueChanged.connect(self._on_bubble_fields_changed)
        cb_t_row.addWidget(QLabel("Mulai"))
        cb_t_row.addWidget(self.cb_start_spin)
        cb_t_row.addWidget(QLabel("Selesai"))
        cb_t_row.addWidget(self.cb_end_spin)
        cb_t_row.addWidget(QLabel("Y"))
        cb_t_row.addWidget(self.cb_y_spin)
        cb_form.addRow("Waktu & Y", cb_t_row)

        cb_av_row = QHBoxLayout()
        self.cb_av_col_btn = ColorSwatchButton("#9146FF")
        self.cb_av_col_btn.color_changed.connect(lambda _c: self._on_bubble_fields_changed())
        self.cb_av_btn = QPushButton("Avatar...")
        self.cb_av_btn.setObjectName("SmallAction")
        self.cb_av_btn.clicked.connect(self._browse_bubble_avatar)
        cb_av_row.addWidget(self.cb_av_col_btn)
        cb_av_row.addWidget(self.cb_av_btn)
        cb_form.addRow("Avatar", cb_av_row)
        cbf.addLayout(cb_form)
        vbox.addWidget(cb_grp)

        # 3. Watermark / Channel Logo
        wm_grp = QGroupBox("Watermark / Logo Channel")
        wmf = QFormLayout(wm_grp)
        self.wm_enabled_chk = QCheckBox("Tampilkan Watermark")
        self.wm_enabled_chk.toggled.connect(self._on_ui_mutated)
        wmf.addRow(self.wm_enabled_chk)

        wm_txt_row = QHBoxLayout()
        self.wm_text_edit = QLineEdit("@ATRO")
        self.wm_text_edit.textChanged.connect(lambda _t: self._on_ui_mutated())
        self.wm_logo_btn = QPushButton("Logo PNG...")
        self.wm_logo_btn.setObjectName("SmallAction")
        self.wm_logo_btn.clicked.connect(self._browse_watermark_logo)
        self.wm_clear_logo_btn = QPushButton("Reset")
        self.wm_clear_logo_btn.setObjectName("SmallAction")
        self.wm_clear_logo_btn.clicked.connect(self._clear_watermark_logo)
        wm_txt_row.addWidget(self.wm_text_edit, 1)
        wm_txt_row.addWidget(self.wm_logo_btn)
        wm_txt_row.addWidget(self.wm_clear_logo_btn)
        wmf.addRow("Teks / Logo", wm_txt_row)

        wm_pos_row = QHBoxLayout()
        self.wm_pos_combo = QComboBox()
        for val, lbl in (
            ("top_right", "Kanan Atas"),
            ("top_left", "Kiri Atas"),
            ("bottom_right", "Kanan Bawah"),
            ("bottom_left", "Kiri Bawah"),
            ("center_split", "Tengah"),
        ):
            self.wm_pos_combo.addItem(lbl, val)
        self.wm_pos_combo.currentIndexChanged.connect(self._on_ui_mutated)
        self.wm_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.wm_opacity_slider.setRange(5, 100)
        self.wm_opacity_slider.valueChanged.connect(self._on_ui_mutated)
        wm_pos_row.addWidget(self.wm_pos_combo)
        wm_pos_row.addWidget(QLabel("Opacity"))
        wm_pos_row.addWidget(self.wm_opacity_slider, 1)
        wmf.addRow("Posisi & Opacity", wm_pos_row)
        vbox.addWidget(wm_grp)

        # 4. Climax Effects
        cl_grp = QGroupBox("Efek Klimaks & Warna")
        clf = QFormLayout(cl_grp)
        cl_chk_row = QHBoxLayout()
        self.climax_contrast_chk = QCheckBox("Subtle Contrast Boost")
        self.climax_contrast_chk.toggled.connect(self._on_ui_mutated)
        self.climax_pbar_chk = QCheckBox("Progress Bar")
        self.climax_pbar_chk.toggled.connect(self._on_ui_mutated)
        self.climax_pbar_col_btn = ColorSwatchButton("#FACC15")
        self.climax_pbar_col_btn.color_changed.connect(lambda _c: self._on_ui_mutated())
        cl_chk_row.addWidget(self.climax_contrast_chk)
        cl_chk_row.addWidget(self.climax_pbar_chk)
        cl_chk_row.addWidget(self.climax_pbar_col_btn)
        clf.addRow(cl_chk_row)

        zp_row = QHBoxLayout()
        self.zoom_punch_chk = QCheckBox("Zoom Punch")
        self.zoom_punch_chk.toggled.connect(self._on_ui_mutated)
        self.zoom_start_spin = QDoubleSpinBox()
        self.zoom_start_spin.setRange(0.0, 600.0)
        self.zoom_start_spin.setSingleStep(0.5)
        self.zoom_start_spin.setSuffix(" s")
        self.zoom_start_spin.valueChanged.connect(self._on_ui_mutated)
        self.zoom_factor_spin = QDoubleSpinBox()
        self.zoom_factor_spin.setRange(1.02, 1.40)
        self.zoom_factor_spin.setSingleStep(0.02)
        self.zoom_factor_spin.setSuffix("x")
        self.zoom_factor_spin.valueChanged.connect(self._on_ui_mutated)
        zp_row.addWidget(self.zoom_punch_chk)
        zp_row.addWidget(QLabel("Mulai"))
        zp_row.addWidget(self.zoom_start_spin)
        zp_row.addWidget(QLabel("Zoom"))
        zp_row.addWidget(self.zoom_factor_spin)
        clf.addRow("Zoom Klimaks", zp_row)

        hdr_row = QHBoxLayout()
        self.vignette_chk = QCheckBox("Vignette Gelap Sinematik")
        self.vignette_chk.toggled.connect(self._on_ui_mutated)
        self.topic_hdr_chk = QCheckBox("Label Topik Atas")
        self.topic_hdr_chk.toggled.connect(self._on_ui_mutated)
        self.topic_hdr_edit = QLineEdit()
        self.topic_hdr_edit.setPlaceholderText("Contoh: CARA CEPAT NAIK LEVEL")
        self.topic_hdr_edit.textChanged.connect(lambda _t: self._on_ui_mutated())
        hdr_row.addWidget(self.vignette_chk)
        hdr_row.addWidget(self.topic_hdr_chk)
        hdr_row.addWidget(self.topic_hdr_edit, 1)
        clf.addRow("Suasana & Label Atas", hdr_row)
        vbox.addWidget(cl_grp)

        # 5. Auto Social Copywriting & Viral Hashtags
        social_grp = QGroupBox("Auto Caption & Hashtag Viral (TikTok / Reels / Shorts)")
        sl = QVBoxLayout(social_grp)
        sl.setSpacing(6)
        self.social_caption_edit = QPlainTextEdit()
        self.social_caption_edit.setMaximumHeight(105)
        self.social_caption_edit.setPlaceholderText("Caption media sosial & hashtag otomatis siap tempel...")
        sl.addWidget(self.social_caption_edit)
        social_btns = QHBoxLayout()
        self.regen_social_btn = QPushButton("Buat Ulang Caption")
        self.regen_social_btn.setObjectName("SmallAction")
        self.regen_social_btn.clicked.connect(self._refresh_social_copywriting)
        self.copy_social_btn = QPushButton("Salin Caption & Hashtag")
        self.copy_social_btn.setObjectName("SmallAction")
        self.copy_social_btn.clicked.connect(self._copy_social_caption)
        social_btns.addWidget(self.regen_social_btn)
        social_btns.addWidget(self.copy_social_btn)
        social_btns.addStretch()
        sl.addLayout(social_btns)
        vbox.addWidget(social_grp)

        vbox.addStretch()
        return w

    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(10)

        self.bgm_enabled_chk = QCheckBox("Aktifkan Backsound Lokal")
        self.bgm_enabled_chk.toggled.connect(self._on_ui_mutated)
        form.addRow(self.bgm_enabled_chk)

        dir_row = QHBoxLayout()
        self.bgm_dir_edit = QLineEdit()
        self.bgm_dir_edit.setReadOnly(True)
        self.bgm_dir_btn = QPushButton("Folder BGM...")
        self.bgm_dir_btn.setObjectName("SmallAction")
        self.bgm_dir_btn.clicked.connect(self._browse_bgm_folder)
        dir_row.addWidget(self.bgm_dir_edit, 1)
        dir_row.addWidget(self.bgm_dir_btn)
        form.addRow("Folder Backsound", dir_row)

        self.bgm_mode_combo = QComboBox()
        self.bgm_mode_combo.addItem("Rotasi Otomatis", "rotate")
        self.bgm_mode_combo.addItem("Acak (Random)", "random")
        self.bgm_mode_combo.addItem("Pilih Track Manual", "manual")
        self.bgm_mode_combo.currentIndexChanged.connect(self._on_ui_mutated)
        form.addRow("Mode Pilih Track", self.bgm_mode_combo)

        track_row = QHBoxLayout()
        self.bgm_track_combo = QComboBox()
        self.bgm_track_combo.currentIndexChanged.connect(self._on_ui_mutated)
        self.bgm_random_pick_btn = QPushButton("Acak Track")
        self.bgm_random_pick_btn.setObjectName("SmallAction")
        self.bgm_random_pick_btn.clicked.connect(self._pick_random_bgm_track)
        track_row.addWidget(self.bgm_track_combo, 1)
        track_row.addWidget(self.bgm_random_pick_btn)
        form.addRow("Track Backsound", track_row)

        vol_row = QHBoxLayout()
        self.bgm_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.bgm_vol_slider.setRange(0, 100)
        self.bgm_vol_lbl = QLabel("14%")
        self.bgm_vol_slider.valueChanged.connect(self._on_ui_mutated)
        vol_row.addWidget(self.bgm_vol_slider, 1)
        vol_row.addWidget(self.bgm_vol_lbl)
        form.addRow("Volume Backsound", vol_row)

        self.audio_ducking_chk = QCheckBox("Audio Ducking (BGM mengecil saat suara bicara)")
        self.audio_ducking_chk.toggled.connect(self._on_ui_mutated)
        form.addRow(self.audio_ducking_chk)

        self.vocal_loudnorm_chk = QCheckBox("Vocal Loudnorm (-16 LUFS)")
        self.vocal_loudnorm_chk.toggled.connect(self._on_ui_mutated)
        form.addRow(self.vocal_loudnorm_chk)

        self.opening_tts_chk = QCheckBox("Suara Opening TTS di Awal Klip")
        self.opening_tts_chk.toggled.connect(self._on_ui_mutated)
        form.addRow(self.opening_tts_chk)
        return w

    def _build_transcript_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(6, 6, 6, 6)
        vbox.setSpacing(8)

        hint = QLabel("Perbaiki typo hasil Whisper dan atur label Speaker (SPEAKER_1 / SPEAKER_2 / ACTION).")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        self.tr_table = QTableWidget(0, 4)
        self.tr_table.setHorizontalHeaderLabels(["Mulai (s)", "Selesai (s)", "Speaker", "Teks Transkrip"])
        hdr = self.tr_table.horizontalHeader()
        if hdr is not None:
            hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tr_table.cellChanged.connect(self._on_transcript_cell_changed)
        vbox.addWidget(self.tr_table, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Tambah Baris")
        add_btn.setObjectName("SmallAction")
        add_btn.clicked.connect(self._add_transcript_row)
        del_btn = QPushButton("Hapus Baris")
        del_btn.setObjectName("SmallAction")
        del_btn.clicked.connect(self._remove_transcript_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        vbox.addLayout(btn_row)
        return w

    # --- Public Data Loading API ---
    def set_batch_preselection(self, clip_indices: Optional[list[int]]) -> None:
        self._preselected_batch_indices = list(clip_indices) if clip_indices is not None else None

    def load_job_context(
        self,
        state: Optional[JobState],
        select_clip_index: Optional[int] = None,
    ) -> None:
        """Load selected JobState artifacts (metadata, transcript, analysis) into Studio."""
        self._pause_playback()
        self._preselected_batch_indices = None
        self._job_state = state
        self._download = None
        self._analysis = None
        self._transcript_segments = []

        self._syncing_ui = True
        self.clip_selector_combo.clear()
        if state is None:
            self._syncing_ui = False
            self._refresh_preview()
            return

        ws_dir = Path(state.workspace_dir)
        meta_path = Path(state.artifacts.metadata_json) if state.artifacts.metadata_json else ws_dir / "metadata.json"
        if meta_path.is_file():
            try:
                self._download = DownloadResult.model_validate_json(meta_path.read_text(encoding="utf-8"))
                self._source_path = Path(self._download.source_video_path)
                self._video_duration_sec = self._download.duration_sec
            except Exception:
                pass

        tr_path = Path(state.artifacts.transcript_json) if state.artifacts.transcript_json else ws_dir / "transcript.json"
        if tr_path.is_file():
            try:
                tr = TranscriptResult.model_validate_json(tr_path.read_text(encoding="utf-8"))
                self._transcript_segments = tr.segments
            except Exception:
                pass

        an_path = Path(state.artifacts.analysis_json) if state.artifacts.analysis_json else ws_dir / "analysis.json"
        if an_path.is_file():
            try:
                self._analysis = AnalysisResult.model_validate_json(an_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if self._analysis and self._analysis.clips:
            target_row = 0
            for idx, clip in enumerate(self._analysis.clips, start=1):
                self.clip_selector_combo.addItem(f"#{idx:02d} — {clip.title}", idx)
                if select_clip_index is not None and idx == select_clip_index:
                    target_row = idx - 1
            self.clip_selector_combo.setCurrentIndex(target_row)
            self._syncing_ui = False
            self._on_clip_selected(target_row)
        else:
            self._syncing_ui = False
            self._refresh_preview()

    def _on_clip_selected(self, combo_idx: int) -> None:
        if self._syncing_ui or combo_idx < 0 or self._analysis is None:
            return
        clip_idx = self.clip_selector_combo.itemData(combo_idx)
        if not isinstance(clip_idx, int) or clip_idx < 1 or clip_idx > len(self._analysis.clips):
            return
        clip = self._analysis.clips[clip_idx - 1]
        self._current_clip_reason = getattr(clip, "reason", "") or ""
        assert self._job_state is not None
        ws_dir = Path(self._job_state.workspace_dir)
        settings = self._settings_provider()
        clip_cfg_file = ws_dir / "clip_configs" / f"clip_{clip_idx:02d}.json"
        cfg = load_or_create_job_clip_config(
            job_dir=ws_dir,
            clip_index=clip_idx,
            clip_title=clip.title,
            start_time=clip.start_time,
            end_time=clip.end_time,
            edit_format=self._job_state.request.edit_format,
            padding_sec=self._job_state.request.padding_sec,
            transcript_segments=self._transcript_segments,
            backsound_dir=str(settings.backsound_dir),
        )
        if not clip_cfg_file.is_file():
            apply_initial_style_overrides(cfg, self._job_state.request.initial_style_overrides)
        cfg.aspect_ratio = self._job_state.request.aspect_ratio
        if cfg.aspect_ratio == "16:9":
            cfg.canvas_width, cfg.canvas_height = 1920, 1080
        self._rel_time_sec = 0.0
        self._load_config_into_ui(cfg)
        self._maybe_schedule_auto_detect()

    def _browse_standalone_video(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Muat Video Lokal ke Studio Preview",
            "",
            "Video Files (*.mp4 *.mkv *.mov *.webm *.avi);;All Files (*)",
        )
        if not chosen:
            return
        path = Path(chosen)
        self._pause_playback()
        self._job_state = None
        self._download = None
        self._analysis = None
        self._source_path = path
        _w, _h, _fps, dur = self._frame_cache.get_video_info(path)
        self._video_duration_sec = dur
        self.clip_selector_combo.blockSignals(True)
        self.clip_selector_combo.clear()
        self.clip_selector_combo.addItem(f"Video Lokal: {path.name}", 1)
        self.clip_selector_combo.blockSignals(False)

        cfg = build_preset_config(
            preset_id=self._config.preset_id,
            clip_title=path.stem,
            start_time=0.0,
            end_time=min(30.0, dur),
            clip_index=1,
            padding_sec=0.0,
            backsound_dir=str(self._settings_provider().backsound_dir),
        )
        if not cfg.transcript_overrides:
            cfg.transcript_overrides = [
                TranscriptSegmentOverride(
                    index=0,
                    start=0.5,
                    end=min(dur, 4.5),
                    text="Contoh subtitle dinamis ATRO Studio *LAUGHS*",
                    speaker="SPEAKER_1",
                )
            ]
        self._rel_time_sec = 0.0
        self._load_config_into_ui(cfg)
        self._maybe_schedule_auto_detect()

    def _maybe_schedule_auto_detect(self) -> None:
        """Run background multi-angle detection only when Studio tab is actually visible."""
        crops = self._config.layout.shot_crops
        needs_detect = (not crops) or any(
            (s.top_crop.label or "").startswith("Angle #") for s in crops
        )
        if (
            self.isVisible()
            and self._config.layout.framing_tracking_mode == "auto_face"
            and needs_detect
            and self._source_path is not None
            and self._source_path.is_file()
            and not self._detect_in_progress
        ):
            self._run_auto_detect_crops()

    def current_config(self) -> ClipComposerConfig:
        """Return a deep copy of the active ClipComposerConfig."""
        return self._config.model_copy(deep=True)

    # --- Preset & JSON Persistence ---
    def apply_preset(self, preset_id: str) -> None:
        """Apply one of READY_TEMPLATES (layout + caption recipe + overlays + audio) in 1 click."""
        new_cfg = build_ready_template_config(
            template_id=preset_id,
            clip_title=self._config.clip_title,
            start_time=self._config.timing.start_time,
            end_time=self._config.timing.end_time,
            clip_index=self._config.clip_index,
            padding_sec=self._config.timing.padding_sec,
            backsound_dir=self._config.audio.backsound_dir or str(self._settings_provider().backsound_dir),
        )
        new_cfg.aspect_ratio = self._config.aspect_ratio
        new_cfg.canvas_width = self._config.canvas_width
        new_cfg.canvas_height = self._config.canvas_height
        new_cfg.transcript_overrides = list(self._config.transcript_overrides)
        self._load_config_into_ui(new_cfg)

    def _refresh_saved_templates_list(self) -> None:
        if not hasattr(self, "saved_templates_list"):
            return
        self.saved_templates_list.clear()
        presets_dir = get_presets_dir()
        for path in sorted(presets_dir.glob("*.json")):
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.saved_templates_list.addItem(item)

    def _on_saved_template_clicked(self, item: QListWidgetItem) -> None:
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if path_str:
            self._apply_loaded_preset_path(Path(path_str))

    def _apply_loaded_preset_path(self, path: Path) -> None:
        try:
            loaded = load_custom_preset(path)
        except Exception as exc:
            QMessageBox.warning(self, "Preset Tidak Valid", f"Gagal memuat preset:\n{exc}")
            return
        # Keep active clip timing/transcript when applying style preset to current clip
        loaded.clip_index = self._config.clip_index
        loaded.timing = self._config.timing.model_copy(deep=True)
        if self._config.transcript_overrides:
            loaded.transcript_overrides = list(self._config.transcript_overrides)
        self._load_config_into_ui(loaded)

    def _save_preset_dialog(self) -> None:
        presets_dir = get_presets_dir()
        default_name = presets_dir / f"{self._config.preset_id}_custom.json"
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Simpan Custom Preset JSON",
            str(default_name),
            "JSON Preset (*.json)",
        )
        if not chosen:
            return
        save_custom_preset(self._config, Path(chosen))
        self._refresh_saved_templates_list()
        QMessageBox.information(self, "Preset Tersimpan", f"Preset JSON disimpan ke:\n{chosen}")

    def _load_preset_dialog(self) -> None:
        presets_dir = get_presets_dir()
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Muat Custom Preset JSON",
            str(presets_dir),
            "JSON Preset (*.json)",
        )
        if not chosen:
            return
        self._apply_loaded_preset_path(Path(chosen))

    # --- Sync Config <-> Controls ---
    def _load_config_into_ui(self, cfg: ClipComposerConfig) -> None:
        self._syncing_ui = True
        self._config = cfg

        for pid, btn in self.preset_buttons.items():
            btn.setChecked(pid == cfg.preset_id)

        active_tid = normalize_ready_template_id(cfg.preset_id) if cfg.preset_id in READY_TEMPLATES else ""
        if hasattr(self, "template_cards"):
            for tid, card in self.template_cards.items():
                card.set_selected(tid == active_tid)

        self.preview_info_lbl.setText(f"{cfg.canvas_width}×{cfg.canvas_height}")
        if hasattr(self, "auto_detect_btn"):
            n_angles = len(cfg.layout.shot_crops)
            self.auto_detect_btn.setText(
                f"Deteksi Wajah ({n_angles} Angle)" if n_angles > 1 else "Deteksi Wajah Otomatis"
            )

        # Timing
        self.trim_start_spin.setValue(cfg.timing.start_time)
        self.trim_end_spin.setValue(cfg.timing.end_time)
        self.trim_pad_spin.setValue(cfg.timing.padding_sec)

        # Layout
        lm_idx = self.layout_mode_combo.findData(cfg.layout.mode)
        self.layout_mode_combo.setCurrentIndex(max(0, lm_idx))
        pct_top = int(round(cfg.layout.split_ratio_top * 100))
        self.split_ratio_slider.setValue(pct_top)
        self.split_ratio_lbl.setText(f"{pct_top}:{100 - pct_top}")
        self.divider_enabled_chk.setChecked(cfg.layout.divider.enabled)
        self.divider_thick_spin.setValue(cfg.layout.divider.thickness_px)
        self.divider_color_btn.set_color_hex(cfg.layout.divider.color_hex)
        self.divider_glow_chk.setChecked(cfg.layout.divider.glow_enabled)
        ang_int = int(round(cfg.layout.divider.angle_deg))
        self.divider_angle_slider.setValue(ang_int)
        self.divider_angle_lbl.setText(f"{ang_int:+d}°" if ang_int != 0 else "0°")
        self.blur_aspect_combo.setCurrentText(cfg.layout.blurred_frame_aspect)
        self.blur_strength_slider.setValue(cfg.layout.background_blur_strength)
        self.blur_strength_lbl.setText(str(cfg.layout.background_blur_strength))
        ps_idx = self.pip_shape_combo.findData(cfg.layout.pip_shape)
        self.pip_shape_combo.setCurrentIndex(max(0, ps_idx))
        pp_idx = self.pip_pos_combo.findData(cfg.layout.pip_position)
        self.pip_pos_combo.setCurrentIndex(max(0, pp_idx))
        pip_pct = int(round(cfg.layout.pip_scale * 100))
        self.pip_scale_slider.setValue(pip_pct)
        self.pip_scale_lbl.setText(f"{pip_pct}%")
        self.frame_corner_spin.setValue(cfg.layout.frame_corner_radius_px)
        self.frame_border_w_spin.setValue(cfg.layout.frame_border_width_px)
        self.frame_border_col_btn.set_color_hex(cfg.layout.frame_border_color_hex)
        self._sync_crop_spins_from_config()

        # Subtitles
        subs = cfg.subtitles
        self.sub_enabled_chk.setChecked(subs.enabled)
        rec_idx = self.recipe_combo.findData(subs.caption_recipe)
        self.recipe_combo.setCurrentIndex(max(0, rec_idx))
        sm_idx = self.sub_style_combo.findData(subs.style_mode)
        self.sub_style_combo.setCurrentIndex(max(0, sm_idx))
        self.sub_offset_spin.setValue(subs.timing_offset_sec)
        self.font_combo.setCurrentText(subs.font_family)
        self.custom_font_lbl.setText(
            Path(subs.custom_font_path).name if subs.custom_font_path else "Font sistem / default"
        )
        self.sub_size_spin.setValue(subs.font_size)
        pl_idx = self.sub_place_combo.findData(subs.placement_preset)
        self.sub_place_combo.setCurrentIndex(max(0, pl_idx))
        self.sub_pos_x_spin.setValue(subs.pos_x)
        self.sub_pos_y_spin.setValue(subs.pos_y)
        self.sub_primary_btn.set_color_hex(subs.primary_color_hex)
        self.sub_highlight_btn.set_color_hex(subs.highlight_color_hex)
        self.sub_outline_btn.set_color_hex(subs.outline_color_hex)
        self.sub_outline_w_spin.setValue(subs.outline_width)
        self.sub_shadow_spin.setValue(subs.shadow_depth)
        self.sub_glow_spin.setValue(subs.glow_strength)
        self.sub_anim_combo.setCurrentText(subs.animation)
        self.sub_upper_chk.setChecked(subs.uppercase)
        self.sub_max_words_spin.setValue(subs.max_words_per_line)
        bgm_mode_idx = self.sub_bg_mode_combo.findData(subs.caption_bg_mode)
        self.sub_bg_mode_combo.setCurrentIndex(max(0, bgm_mode_idx))
        self.sub_pill_col_btn.set_color_hex(subs.active_word_bg_hex)
        self.sub_pill_radius_spin.setValue(subs.active_word_bg_radius)
        self.sub_dual_kw_chk.setChecked(subs.dual_keyword_enabled)
        self.sub_sec_col_btn.set_color_hex(subs.secondary_keyword_color_hex)
        self.sub_auto_emoji_chk.setChecked(subs.auto_emoji_enabled)
        self.multi_speaker_chk.setChecked(subs.multi_speaker_enabled)
        spk2 = subs.speaker_palettes.get("SPEAKER_2")
        self.spk2_hl_btn.set_color_hex(spk2.highlight_hex if spk2 else "#38BDF8")
        self.action_col_btn.set_color_hex(subs.action_token_color_hex)
        self.footnote_chk.setChecked(subs.footnote.enabled)
        self.footnote_input.setText(subs.footnote.text)

        # Overlays
        hb = cfg.overlays.hook_banner
        self.hook_enabled_chk.setChecked(hb.enabled)
        self.hook_text_edit.setText(hb.text)
        self.hook_size_spin.setValue(hb.font_size)
        self.hook_dur_spin.setValue(hb.duration_sec)
        self.hook_y_spin.setValue(hb.pos_y)
        self.hook_bg_btn.set_color_hex(hb.bg_color_hex)
        self.hook_txt_btn.set_color_hex(hb.text_color_hex)
        self.hook_acc_btn.set_color_hex(hb.accent_color_hex)
        self.hook_tr_in_combo.setCurrentText(hb.transition_in)
        self.hook_tr_out_combo.setCurrentText(hb.transition_out)

        self._reload_comment_bubble_list()

        wm = cfg.overlays.watermark
        self.wm_enabled_chk.setChecked(wm.enabled)
        self.wm_text_edit.setText(wm.text)
        wm_idx = self.wm_pos_combo.findData(wm.position)
        self.wm_pos_combo.setCurrentIndex(max(0, wm_idx))
        self.wm_opacity_slider.setValue(int(round(wm.opacity * 100)))

        cl = cfg.overlays.climax_effects
        self.climax_contrast_chk.setChecked(cl.contrast_boost_enabled)
        self.climax_pbar_chk.setChecked(cl.progress_bar_enabled)
        self.climax_pbar_col_btn.set_color_hex(cl.progress_bar_color_hex)
        self.zoom_punch_chk.setChecked(cl.zoom_punch_enabled)
        self.zoom_start_spin.setValue(cl.zoom_punch_start_sec)
        self.zoom_factor_spin.setValue(cl.zoom_punch_factor)
        self.vignette_chk.setChecked(cl.vignette_enabled)
        self.topic_hdr_chk.setChecked(cl.topic_header_enabled)
        self.topic_hdr_edit.setText(cl.topic_header_text)
        self._refresh_social_copywriting()

        # Audio
        au = cfg.audio
        self.bgm_enabled_chk.setChecked(au.backsound_enabled)
        bgm_dir = au.backsound_dir or str(self._settings_provider().backsound_dir)
        self.bgm_dir_edit.setText(bgm_dir)
        bm_idx = self.bgm_mode_combo.findData(au.selection_mode)
        self.bgm_mode_combo.setCurrentIndex(max(0, bm_idx))
        self._refresh_bgm_tracks_combo(bgm_dir, au.selected_track_filename)
        vol_pct = int(round(au.backsound_volume * 100))
        self.bgm_vol_slider.setValue(vol_pct)
        self.bgm_vol_lbl.setText(f"{vol_pct}%")
        self.audio_ducking_chk.setChecked(au.audio_ducking)
        self.vocal_loudnorm_chk.setChecked(au.vocal_loudnorm)
        self.opening_tts_chk.setChecked(cfg.timing.opening_tts_enabled)

        # Transcript Table
        self._reload_transcript_table()

        self._syncing_ui = False
        self._refresh_preview()

    def _sync_crop_spins_from_config(self) -> None:
        tc, bc = self._config.layout.resolve_crops_at_time(self._rel_time_sec)
        for sp, val in (
            (self.top_x, tc.x),
            (self.top_y, tc.y),
            (self.top_w, tc.w),
            (self.top_h, tc.h),
            (self.bot_x, bc.x),
            (self.bot_y, bc.y),
            (self.bot_w, bc.w),
            (self.bot_h, bc.h),
        ):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)

    def _on_crop_dragged(self) -> None:
        self._sync_crop_spins_from_config()
        self._refresh_preview()

    def _on_crop_spin_changed(self) -> None:
        if self._syncing_ui:
            return
        tc = self._config.layout.top_crop
        tc.auto_detect = False
        tc.x = self.top_x.value()
        tc.y = self.top_y.value()
        tc.w = min(1.0 - tc.x, self.top_w.value())
        tc.h = min(1.0 - tc.y, self.top_h.value())

        bc = self._config.layout.bottom_crop
        bc.auto_detect = False
        bc.x = self.bot_x.value()
        bc.y = self.bot_y.value()
        bc.w = min(1.0 - bc.x, self.bot_w.value())
        bc.h = min(1.0 - bc.y, self.bot_h.value())

        self._config.layout.framing_tracking_mode = "manual_crop"
        self._refresh_preview()

    def _on_preview_overlay_dragged(self) -> None:
        self._syncing_ui = True
        self.sub_pos_x_spin.setValue(self._config.subtitles.pos_x)
        self.sub_pos_y_spin.setValue(self._config.subtitles.pos_y)
        pl_idx = self.sub_place_combo.findData(self._config.subtitles.placement_preset)
        self.sub_place_combo.setCurrentIndex(max(0, pl_idx))
        self.hook_y_spin.setValue(self._config.overlays.hook_banner.pos_y)
        row = self.cb_list.currentRow()
        if 0 <= row < len(self._config.overlays.comment_bubbles):
            self.cb_y_spin.setValue(self._config.overlays.comment_bubbles[row].pos_y)
        self._syncing_ui = False
        self._refresh_preview()

    def _on_placement_preset_changed(self) -> None:
        if self._syncing_ui:
            return
        preset = self.sub_place_combo.currentData() or "bottom_safe"
        self._config.subtitles.placement_preset = preset
        self._config.sync_subtitle_anchor()
        self._syncing_ui = True
        self.sub_pos_x_spin.setValue(self._config.subtitles.pos_x)
        self.sub_pos_y_spin.setValue(self._config.subtitles.pos_y)
        self._syncing_ui = False
        self._refresh_preview()

    def _on_ui_mutated(self) -> None:
        if self._syncing_ui:
            return
        cfg = self._config
        # Layout
        cfg.layout.mode = self.layout_mode_combo.currentData() or "single_focus"
        pct = self.split_ratio_slider.value()
        cfg.layout.split_ratio_top = pct / 100.0
        self.split_ratio_lbl.setText(f"{pct}:{100 - pct}")
        cfg.layout.divider.enabled = self.divider_enabled_chk.isChecked()
        cfg.layout.divider.thickness_px = self.divider_thick_spin.value()
        cfg.layout.divider.color_hex = self.divider_color_btn.color_hex()
        cfg.layout.divider.glow_enabled = self.divider_glow_chk.isChecked()
        ang = self.divider_angle_slider.value()
        cfg.layout.divider.angle_deg = float(ang)
        self.divider_angle_lbl.setText(f"{ang:+d}°" if ang != 0 else "0°")
        cfg.layout.blurred_frame_aspect = self.blur_aspect_combo.currentText()  # type: ignore[assignment]
        blur_v = self.blur_strength_slider.value()
        cfg.layout.background_blur_strength = blur_v
        self.blur_strength_lbl.setText(str(blur_v))
        cfg.layout.pip_shape = self.pip_shape_combo.currentData() or "circle"
        cfg.layout.pip_position = self.pip_pos_combo.currentData() or "top_right"
        pip_pct = self.pip_scale_slider.value()
        cfg.layout.pip_scale = pip_pct / 100.0
        self.pip_scale_lbl.setText(f"{pip_pct}%")
        cfg.layout.frame_corner_radius_px = self.frame_corner_spin.value()
        cfg.layout.frame_border_width_px = self.frame_border_w_spin.value()
        cfg.layout.frame_border_color_hex = self.frame_border_col_btn.color_hex()

        # Subtitles
        subs = cfg.subtitles
        subs.enabled = self.sub_enabled_chk.isChecked()
        subs.style_mode = self.sub_style_combo.currentData() or "karaoke_highlight"
        subs.timing_offset_sec = round(self.sub_offset_spin.value(), 2)
        subs.font_family = self.font_combo.currentText().strip() or "Arial Black"
        subs.font_size = self.sub_size_spin.value()
        subs.pos_x = self.sub_pos_x_spin.value()
        subs.pos_y = self.sub_pos_y_spin.value()
        if subs.placement_preset == "split_line_center":
            cfg.sync_subtitle_anchor()
            self._syncing_ui = True
            self.sub_pos_y_spin.setValue(subs.pos_y)
            self._syncing_ui = False
        subs.primary_color_hex = self.sub_primary_btn.color_hex()
        subs.highlight_color_hex = self.sub_highlight_btn.color_hex()
        subs.outline_color_hex = self.sub_outline_btn.color_hex()
        subs.outline_width = self.sub_outline_w_spin.value()
        subs.shadow_depth = self.sub_shadow_spin.value()
        subs.glow_strength = self.sub_glow_spin.value()
        subs.animation = self.sub_anim_combo.currentText()  # type: ignore[assignment]
        subs.uppercase = self.sub_upper_chk.isChecked()
        subs.max_words_per_line = self.sub_max_words_spin.value()
        subs.caption_bg_mode = self.sub_bg_mode_combo.currentData() or "none"
        subs.active_word_bg_hex = self.sub_pill_col_btn.color_hex()
        subs.active_word_bg_radius = self.sub_pill_radius_spin.value()
        subs.dual_keyword_enabled = self.sub_dual_kw_chk.isChecked()
        subs.secondary_keyword_color_hex = self.sub_sec_col_btn.color_hex()
        subs.auto_emoji_enabled = self.sub_auto_emoji_chk.isChecked()
        subs.multi_speaker_enabled = self.multi_speaker_chk.isChecked()
        if "SPEAKER_2" in subs.speaker_palettes:
            subs.speaker_palettes["SPEAKER_2"].highlight_hex = self.spk2_hl_btn.color_hex()
        subs.action_token_color_hex = self.action_col_btn.color_hex()
        subs.footnote.enabled = self.footnote_chk.isChecked()
        subs.footnote.text = self.footnote_input.text()

        # Overlays
        hb = cfg.overlays.hook_banner
        hb.enabled = self.hook_enabled_chk.isChecked()
        hb.text = self.hook_text_edit.text()
        hb.font_size = self.hook_size_spin.value()
        hb.duration_sec = self.hook_dur_spin.value()
        hb.pos_y = self.hook_y_spin.value()
        hb.bg_color_hex = self.hook_bg_btn.color_hex()
        hb.text_color_hex = self.hook_txt_btn.color_hex()
        hb.accent_color_hex = self.hook_acc_btn.color_hex()
        hb.transition_in = self.hook_tr_in_combo.currentText()  # type: ignore[assignment]
        hb.transition_out = self.hook_tr_out_combo.currentText()  # type: ignore[assignment]

        wm = cfg.overlays.watermark
        wm.enabled = self.wm_enabled_chk.isChecked()
        wm.text = self.wm_text_edit.text()
        wm.position = self.wm_pos_combo.currentData() or "top_right"
        wm.opacity = self.wm_opacity_slider.value() / 100.0

        cl = cfg.overlays.climax_effects
        cl.contrast_boost_enabled = self.climax_contrast_chk.isChecked()
        cl.progress_bar_enabled = self.climax_pbar_chk.isChecked()
        cl.progress_bar_color_hex = self.climax_pbar_col_btn.color_hex()
        cl.zoom_punch_enabled = self.zoom_punch_chk.isChecked()
        cl.zoom_punch_start_sec = self.zoom_start_spin.value()
        cl.zoom_punch_factor = self.zoom_factor_spin.value()
        cl.vignette_enabled = self.vignette_chk.isChecked()
        cl.topic_header_enabled = self.topic_hdr_chk.isChecked()
        cl.topic_header_text = self.topic_hdr_edit.text()

        # Audio
        au = cfg.audio
        au.backsound_enabled = self.bgm_enabled_chk.isChecked()
        au.selection_mode = self.bgm_mode_combo.currentData() or "rotate"
        au.selected_track_filename = self.bgm_track_combo.currentData()
        vol_pct = self.bgm_vol_slider.value()
        au.backsound_volume = vol_pct / 100.0
        self.bgm_vol_lbl.setText(f"{vol_pct}%")
        au.audio_ducking = self.audio_ducking_chk.isChecked()
        au.vocal_loudnorm = self.vocal_loudnorm_chk.isChecked()
        cfg.timing.opening_tts_enabled = self.opening_tts_chk.isChecked()

        self._refresh_preview()

    # --- Comment Bubble CRUD ---
    def _reload_comment_bubble_list(self) -> None:
        self.cb_list.blockSignals(True)
        self.cb_list.clear()
        for b in self._config.overlays.comment_bubbles:
            item = QListWidgetItem(f"{b.author_label} {b.username} [{b.start_sec:.1f}-{b.end_sec:.1f}s]")
            self.cb_list.addItem(item)
        self.cb_list.blockSignals(False)
        if self._config.overlays.comment_bubbles:
            self.cb_list.setCurrentRow(0)
            self._on_bubble_selected(0)
        else:
            self._on_bubble_selected(-1)

    def _on_bubble_selected(self, row: int) -> None:
        bubbles = self._config.overlays.comment_bubbles
        valid = 0 <= row < len(bubbles)
        for w in (
            self.cb_enabled_chk,
            self.cb_label_edit,
            self.cb_user_edit,
            self.cb_text_edit,
            self.cb_start_spin,
            self.cb_end_spin,
            self.cb_y_spin,
            self.cb_av_col_btn,
            self.cb_av_btn,
        ):
            w.setEnabled(valid)
        if not valid:
            return
        b = bubbles[row]
        prev = self._syncing_ui
        self._syncing_ui = True
        self.cb_enabled_chk.setChecked(b.enabled)
        self.cb_label_edit.setText(b.author_label)
        self.cb_user_edit.setText(b.username)
        self.cb_text_edit.setText(b.text)
        self.cb_start_spin.setValue(b.start_sec)
        self.cb_end_spin.setValue(b.end_sec)
        self.cb_y_spin.setValue(b.pos_y)
        self.cb_av_col_btn.set_color_hex(b.avatar_color_hex)
        self._syncing_ui = prev

    def _on_bubble_fields_changed(self) -> None:
        if self._syncing_ui:
            return
        row = self.cb_list.currentRow()
        bubbles = self._config.overlays.comment_bubbles
        if not (0 <= row < len(bubbles)):
            return
        b = bubbles[row]
        b.enabled = self.cb_enabled_chk.isChecked()
        b.author_label = self.cb_label_edit.text()
        b.username = self.cb_user_edit.text()
        b.text = self.cb_text_edit.text()
        b.start_sec = self.cb_start_spin.value()
        b.end_sec = max(b.start_sec + 0.2, self.cb_end_spin.value())
        b.pos_y = self.cb_y_spin.value()
        b.avatar_color_hex = self.cb_av_col_btn.color_hex()
        item = self.cb_list.item(row)
        if item is not None:
            item.setText(f"{b.author_label} {b.username} [{b.start_sec:.1f}-{b.end_sec:.1f}s]")
        self._refresh_preview()

    def _add_comment_bubble(self) -> None:
        idx = len(self._config.overlays.comment_bubbles) + 1
        new_b = CommentBubbleItem(
            id=f"cb_{idx}",
            start_sec=round(self._rel_time_sec, 1),
            end_sec=round(self._rel_time_sec + 3.5, 1),
        )
        self._config.overlays.comment_bubbles.append(new_b)
        self._reload_comment_bubble_list()
        self.cb_list.setCurrentRow(len(self._config.overlays.comment_bubbles) - 1)
        self._refresh_preview()

    def _remove_comment_bubble(self) -> None:
        row = self.cb_list.currentRow()
        if 0 <= row < len(self._config.overlays.comment_bubbles):
            self._config.overlays.comment_bubbles.pop(row)
            self._reload_comment_bubble_list()
            self._refresh_preview()

    def _browse_bubble_avatar(self) -> None:
        row = self.cb_list.currentRow()
        if not (0 <= row < len(self._config.overlays.comment_bubbles)):
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Pilih Avatar Comment Bubble", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if chosen:
            self._config.overlays.comment_bubbles[row].avatar_path = chosen
            self._refresh_preview()

    # --- File Pickers & Auto-Detect ---
    def _browse_font_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Pilih Font Subtitle Lokal", "", "Fonts (*.ttf *.otf)"
        )
        if chosen:
            self._config.subtitles.custom_font_path = chosen
            self.custom_font_lbl.setText(Path(chosen).name)
            self._refresh_preview()

    def _browse_watermark_logo(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Pilih Logo Watermark PNG", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if chosen:
            self._config.overlays.watermark.logo_path = chosen
            self._config.overlays.watermark.enabled = True
            self.wm_enabled_chk.setChecked(True)
            self._refresh_preview()

    def _clear_watermark_logo(self) -> None:
        self._config.overlays.watermark.logo_path = None
        self._refresh_preview()

    def _browse_bgm_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Pilih Folder Backsound Lokal")
        if chosen:
            self._config.audio.backsound_dir = chosen
            self.bgm_dir_edit.setText(chosen)
            self._refresh_bgm_tracks_combo(chosen, self._config.audio.selected_track_filename)

    def _refresh_bgm_tracks_combo(self, bgm_dir_str: str, selected: Optional[str]) -> None:
        self.bgm_track_combo.blockSignals(True)
        self.bgm_track_combo.clear()
        tracks = scan_backsound_tracks(Path(bgm_dir_str)) if bgm_dir_str else []
        if not tracks:
            self.bgm_track_combo.addItem("(Folder kosong)", None)
        else:
            for t in tracks:
                self.bgm_track_combo.addItem(t.name, t.name)
            if selected:
                idx = self.bgm_track_combo.findData(selected)
                if idx >= 0:
                    self.bgm_track_combo.setCurrentIndex(idx)
        self.bgm_track_combo.blockSignals(False)

    def _pick_random_bgm_track(self) -> None:
        import random

        count = self.bgm_track_combo.count()
        if count <= 0 or self.bgm_track_combo.itemData(0) is None:
            return
        idx = random.randrange(0, count)
        self.bgm_mode_combo.setCurrentIndex(self.bgm_mode_combo.findData("manual"))
        self.bgm_track_combo.setCurrentIndex(idx)
        self._on_ui_mutated()

    def _run_auto_detect_crops(self) -> None:
        if self._source_path is None or not self._source_path.is_file():
            return
        p_start, p_end = self._effective_window()
        src_path = self._source_path
        mode = self._config.layout.mode
        src_w = self._download.width if self._download else 1920
        src_h = self._download.height if self._download else 1080
        fps = self._download.fps if self._download else 30.0

        self._detect_request_id += 1
        req_id = self._detect_request_id
        self._detect_in_progress = True
        self._config.layout.framing_tracking_mode = "auto_face"
        self._config.layout.top_crop.auto_detect = True
        self._config.layout.bottom_crop.auto_detect = True
        self.auto_detect_btn.setEnabled(False)
        self.auto_detect_btn.setText("Mendeteksi Angle...")

        def _worker() -> None:
            try:
                segs = detect_composer_shot_crops_sync(
                    source_path=src_path,
                    start_sec=p_start,
                    end_sec=p_end,
                    mode=mode,
                    src_w=src_w,
                    src_h=src_h,
                    fps=fps,
                )
            except Exception:
                segs = []
            self._auto_detect_done.emit(req_id, str(src_path), segs)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_auto_detect_finished(self, req_id: int, src_str: str, segs_obj: object) -> None:
        if req_id != self._detect_request_id:
            return
        self._detect_in_progress = False
        self.auto_detect_btn.setEnabled(True)

        segs = list(segs_obj) if isinstance(segs_obj, list) else []
        if segs and self._source_path is not None and str(self._source_path) == src_str:
            lay = self._config.layout
            lay.shot_crops = segs
            lay.framing_tracking_mode = "auto_face"
            if lay.top_crop.auto_detect:
                lay.top_crop = segs[0].top_crop.model_copy(deep=True)
            if lay.bottom_crop.auto_detect and segs[0].bottom_crop is not None:
                lay.bottom_crop = segs[0].bottom_crop.model_copy(deep=True)
            if self._job_state is not None:
                save_job_clip_config(Path(self._job_state.workspace_dir), self._config)
            self.auto_detect_btn.setText(
                f"Deteksi Wajah ({len(segs)} Angle)" if len(segs) > 1 else "Deteksi Wajah Otomatis"
            )
            self._sync_crop_spins_from_config()
            self._refresh_preview()
        else:
            self.auto_detect_btn.setText("Deteksi Wajah Otomatis")

    # --- Transcript Table Editing ---
    def _reload_transcript_table(self) -> None:
        self.tr_table.blockSignals(True)
        self.tr_table.setRowCount(0)
        for ov in self._config.transcript_overrides:
            self._insert_transcript_row(ov)
        self.tr_table.blockSignals(False)

    def _insert_transcript_row(self, ov: TranscriptSegmentOverride) -> None:
        row = self.tr_table.rowCount()
        self.tr_table.insertRow(row)
        self.tr_table.setItem(row, 0, QTableWidgetItem(f"{ov.start:.2f}"))
        self.tr_table.setItem(row, 1, QTableWidgetItem(f"{ov.end:.2f}"))

        spk_combo = QComboBox()
        spk_combo.addItems(["SPEAKER_1", "SPEAKER_2", "ACTION"])
        spk_combo.setCurrentText(ov.speaker or "SPEAKER_1")
        spk_combo.currentTextChanged.connect(lambda _t: self._sync_transcript_from_table())
        self.tr_table.setCellWidget(row, 2, spk_combo)

        self.tr_table.setItem(row, 3, QTableWidgetItem(ov.text))

    def _on_transcript_cell_changed(self, _row: int, _col: int) -> None:
        if not self._syncing_ui:
            self._sync_transcript_from_table()

    def _sync_transcript_from_table(self) -> None:
        old_overrides = list(self._config.transcript_overrides)
        overrides: list[TranscriptSegmentOverride] = []
        for r in range(self.tr_table.rowCount()):
            try:
                st_it = self.tr_table.item(r, 0)
                en_it = self.tr_table.item(r, 1)
                tx_it = self.tr_table.item(r, 3)
                spk_w = self.tr_table.cellWidget(r, 2)
                st = max(0.0, float(st_it.text()) if st_it else 0.0)
                en = max(st + 0.2, float(en_it.text()) if en_it else st + 2.0)
                txt = (tx_it.text() if tx_it else "").strip()
                spk = spk_w.currentText() if isinstance(spk_w, QComboBox) else "SPEAKER_1"
                old_words = (
                    list(old_overrides[r].words)
                    if r < len(old_overrides)
                    and len(old_overrides[r].words) == len(txt.split())
                    and abs(old_overrides[r].start - st) < 0.05
                    and abs(old_overrides[r].end - en) < 0.05
                    else []
                )
                if txt:
                    overrides.append(
                        TranscriptSegmentOverride(
                            index=r,
                            start=st,
                            end=en,
                            text=txt,
                            speaker=spk,
                            words=old_words,
                        )
                    )
            except Exception:
                continue
        self._config.transcript_overrides = overrides
        self._refresh_preview()

    def _add_transcript_row(self) -> None:
        p_start, _ = self._effective_window()
        st = round(p_start + self._rel_time_sec, 2)
        ov = TranscriptSegmentOverride(
            index=len(self._config.transcript_overrides),
            start=st,
            end=round(st + 2.5, 2),
            text="Teks subtitle baru",
            speaker="SPEAKER_1",
        )
        self.tr_table.blockSignals(True)
        self._insert_transcript_row(ov)
        self.tr_table.blockSignals(False)
        self._sync_transcript_from_table()

    def _remove_transcript_row(self) -> None:
        row = self.tr_table.currentRow()
        if row >= 0:
            self.tr_table.removeRow(row)
            self._sync_transcript_from_table()

    # --- Timeline & Playback ---
    def _effective_window(self) -> tuple[float, float]:
        pad = max(0.0, float(self._config.timing.padding_sec))
        p_start = max(0.0, round(self._config.timing.start_time - pad, 3))
        p_end = min(self._video_duration_sec, round(self._config.timing.end_time + pad, 3))
        if p_end <= p_start:
            p_end = round(p_start + 1.0, 3)
        return p_start, p_end

    def _on_trim_changed(self) -> None:
        if self._syncing_ui:
            return
        st = self.trim_start_spin.value()
        en = max(st + 0.5, self.trim_end_spin.value())
        pad = self.trim_pad_spin.value()
        self._config.timing.start_time = round(st, 3)
        self._config.timing.end_time = round(en, 3)
        self._config.timing.padding_sec = round(pad, 3)
        if self._transcript_segments and not self._config.transcript_overrides:
            self._config.transcript_overrides = extract_transcript_overrides(
                self._transcript_segments, st, en, pad
            )
            self._reload_transcript_table()
        p_start, p_end = self._effective_window()
        dur = max(0.5, p_end - p_start)
        self._rel_time_sec = min(dur, max(0.0, self._rel_time_sec))
        self._refresh_preview()

    def _toggle_playback(self) -> None:
        if self._is_playing:
            self._pause_playback()
        else:
            self._is_playing = True
            self.play_btn.setText("Jeda Preview")
            self._play_timer.start()

    def _pause_playback(self) -> None:
        self._is_playing = False
        self._play_timer.stop()
        self.play_btn.setText("Putar Preview")

    def _on_playback_tick(self) -> None:
        p_start, p_end = self._effective_window()
        dur = max(0.5, p_end - p_start)
        self._rel_time_sec += 0.033
        if self._rel_time_sec >= dur:
            self._rel_time_sec = 0.0
        self._sync_timeline_slider(dur)
        self._refresh_preview()

    def _on_slider_scrubbed(self, val: int) -> None:
        if self._syncing_ui:
            return
        p_start, p_end = self._effective_window()
        dur = max(0.5, p_end - p_start)
        self._rel_time_sec = (val / 1000.0) * dur
        self._refresh_preview()

    def _sync_timeline_slider(self, dur: float) -> None:
        self.timeline_slider.blockSignals(True)
        self.timeline_slider.setValue(int(round(max(0.0, min(1.0, self._rel_time_sec / max(0.1, dur))) * 1000)))
        self.timeline_slider.blockSignals(False)

    def _refresh_preview(self) -> None:
        p_start, p_end = self._effective_window()
        dur = max(0.5, p_end - p_start)
        self._rel_time_sec = max(0.0, min(dur, self._rel_time_sec))
        self._sync_timeline_slider(dur)
        self.time_readout_lbl.setText(f"{self._rel_time_sec:05.2f}s / {dur:05.2f}s")

        src_ts = p_start + self._rel_time_sec
        frame_bgr = self._frame_cache.get_frame(self._source_path, src_ts)
        self.crop_selector.set_state(self._config, frame_bgr, rel_time_sec=self._rel_time_sec)
        self._sync_crop_spins_from_config()
        self.preview_canvas.render_state(
            source_frame_bgr=frame_bgr,
            config=self._config,
            rel_time_sec=self._rel_time_sec,
            segments=self._transcript_segments,
            clip_start_sec=p_start,
            clip_end_sec=p_end,
        )

    def _on_export_clicked(self) -> None:
        self._pause_playback()
        cfg = self.current_config()
        if self._job_state is not None:
            save_job_clip_config(Path(self._job_state.workspace_dir), cfg)
            self.render_requested.emit(self._job_state.job_id, cfg)
        elif self._source_path is not None and self._source_path.is_file():
            self.standalone_render_requested.emit(str(self._source_path), cfg)
        else:
            QMessageBox.information(
                self,
                "Belum Ada Video",
                "Pilih job klip yang sudah diproses atau klik 'Muat Video...' terlebih dahulu.",
            )

    def apply_current_style_to_clips(
        self,
        clip_indices: list[int],
        reset_auto_face: bool = True,
    ) -> int:
        """Apply active Studio style & layout settings to specified clips and persist JSON."""
        if self._job_state is None or self._analysis is None or not self._analysis.clips:
            return 0
        ws_dir = Path(self._job_state.workspace_dir)
        settings = self._settings_provider()
        style_cfg = self.current_config()
        updated = 0
        for idx in dict.fromkeys(clip_indices):
            if idx < 1 or idx > len(self._analysis.clips):
                continue
            if idx == style_cfg.clip_index:
                target_cfg = style_cfg.model_copy(deep=True)
                if reset_auto_face and target_cfg.layout.framing_tracking_mode == "auto_face":
                    target_cfg.layout.top_crop.auto_detect = True
                    target_cfg.layout.bottom_crop.auto_detect = True
                    target_cfg.layout.shot_crops = []
            else:
                clip = self._analysis.clips[idx - 1]
                cfg_file = ws_dir / "clip_configs" / f"clip_{idx:02d}.json"
                existed = cfg_file.is_file()
                target_cfg = load_or_create_job_clip_config(
                    job_dir=ws_dir,
                    clip_index=idx,
                    clip_title=clip.title,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    edit_format=self._job_state.request.edit_format,
                    padding_sec=self._job_state.request.padding_sec,
                    transcript_segments=self._transcript_segments,
                    backsound_dir=str(settings.backsound_dir),
                )
                if not existed:
                    apply_initial_style_overrides(
                        target_cfg,
                        self._job_state.request.initial_style_overrides,
                    )
                apply_style_template_from_config(
                    style_cfg,
                    target_cfg,
                    reset_auto_face=reset_auto_face,
                )
            save_job_clip_config(ws_dir, target_cfg)
            updated += 1
        return updated

    def _on_apply_style_all_clicked(self) -> None:
        if self._job_state is None or self._analysis is None or not self._analysis.clips:
            QMessageBox.information(
                self,
                "Belum Ada Job",
                "Pilih job yang sudah selesai dari Daftar Job untuk menerapkan gaya ke semua klip.",
            )
            return
        all_indices = list(range(1, len(self._analysis.clips) + 1))
        count = self.apply_current_style_to_clips(all_indices, reset_auto_face=True)
        QMessageBox.information(
            self,
            "Gaya Diterapkan ke Semua Klip",
            f"Gaya Studio saat ini telah disimpan ke {count} klip.\n\n"
            "Klik 'Edit & Render Beberapa/Semua...' untuk langsung merender ulang videonya.",
        )

    def open_batch_render_dialog(self, preselected_indices: Optional[list[int]] = None) -> None:
        if self._job_state is None or self._analysis is None or not self._analysis.clips:
            QMessageBox.information(
                self,
                "Belum Ada Klip Job",
                "Batch edit/render tersedia untuk klip dari job yang sudah selesai ditranscribe & dipotong.",
            )
            return
        self._pause_playback()
        pre = (
            preselected_indices
            if preselected_indices is not None
            else self._preselected_batch_indices
        )
        dlg = BatchClipRenderDialog(
            self._analysis.clips,
            preselected_indices=pre,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        indices = dlg.selected_clip_indices()
        apply_style = dlg.should_apply_style()
        redetect = dlg.should_redetect_faces()
        if dlg.action_mode == "save_only":
            if apply_style:
                count = self.apply_current_style_to_clips(indices, reset_auto_face=redetect)
                QMessageBox.information(
                    self,
                    "Pengaturan Batch Disimpan",
                    f"Gaya Studio disimpan ke {count} klip terpilih.",
                )
            return

        style_cfg = self.current_config() if apply_style else None
        if style_cfg is not None:
            # Persist active clip changes if active clip was edited before dispatching batch
            save_job_clip_config(Path(self._job_state.workspace_dir), style_cfg)
        self.batch_render_requested.emit(self._job_state.job_id, indices, style_cfg, redetect)

    def _on_caption_recipe_selected(self) -> None:
        if self._syncing_ui:
            return
        recipe_id = self.recipe_combo.currentData() or "hormozi_viral"
        apply_caption_recipe(self._config, str(recipe_id))
        self._load_config_into_ui(self._config)

    def _refresh_social_copywriting(self) -> None:
        title = self._config.overlays.hook_banner.text.strip() or self._config.clip_title
        copy_pack = generate_social_copywriting(
            clip_title=title,
            reason=self._current_clip_reason,
            preset_id=self._config.preset_id,
        )
        self.social_caption_edit.setPlainText(copy_pack["full_post"])

    def _copy_social_caption(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.social_caption_edit.toPlainText())
            old = self.copy_social_btn.text()
            self.copy_social_btn.setText("Tersalin!")
            QTimer.singleShot(1400, lambda: self.copy_social_btn.setText(old))

    def closeEvent(self, event) -> None:
        self._pause_playback()
        self._frame_cache.close()
        super().closeEvent(event)
