"""Settings view & dialog for AI provider, Whisper Anti-Typo, Audio, TTS, and workspace."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from atro.config import AppSettings
from atro.modules.cutter.opening_narrator import (
    TTS_STYLE_PRESETS,
    TTS_VOICE_CHOICES,
    resolve_tts_prosody,
    synthesize_opening_voiceover,
)
from atro.ui.components import ToggleSwitch, play_audio_preview_file

_OPENAI_PRESETS: dict[str, tuple[str, str]] = {
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "OpenRouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "Groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "Ollama (Lokal)": ("http://localhost:11434/v1", "qwen2.5:7b"),
    "LM Studio (Lokal)": ("http://localhost:1234/v1", "local-model"),
}


class SettingsWidget(QWidget):
    """Embeddable Creator Studio settings page structured as sleek cards."""

    settings_saved = pyqtSignal(object)
    cancelled = pyqtSignal()
    _tts_preview_ready = pyqtSignal(str, float, str)

    def __init__(
        self,
        settings: AppSettings,
        embedded: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings.model_copy(deep=True)
        self._embedded = embedded
        self._suppress_preset_fill = False
        self._tts_player = None
        self._tts_audio_output = None
        self._tts_preview_ready.connect(self._on_tts_preview_ready)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20 if embedded else 16, 16, 20 if embedded else 16, 14)
        outer.setSpacing(12)

        # Page Header
        hdr_row = QHBoxLayout()
        hdr_col = QVBoxLayout()
        hdr_col.setSpacing(2)
        title = QLabel("Pengaturan Studio & Pipeline")
        title.setObjectName("HeroTitle" if embedded else "SectionTitle")
        subtitle = QLabel(
            "Konfigurasi AI Provider (Gemini / OpenAI-Compatible), model Whisper anti-typo, backsound, TTS, dan workspace."
        )
        subtitle.setObjectName("MutedText")
        subtitle.setWordWrap(True)
        hdr_col.addWidget(title)
        hdr_col.addWidget(subtitle)
        hdr_row.addLayout(hdr_col, 1)
        outer.addLayout(hdr_row)

        # Scrollable cards body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_body = QWidget()
        body = QVBoxLayout(scroll_body)
        body.setContentsMargins(0, 0, 6, 0)
        body.setSpacing(14)

        # ---- Card 1: AI Provider ----
        ai_card = QFrame()
        ai_card.setObjectName("Card")
        ai_lay = QVBoxLayout(ai_card)
        ai_lay.setContentsMargins(16, 14, 16, 14)
        ai_lay.setSpacing(10)

        ai_title = QLabel("1. AI Provider & Model Analisis")
        ai_title.setObjectName("SectionTitle")
        ai_lay.addWidget(ai_title)

        provider_form = QFormLayout()
        provider_form.setSpacing(10)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Google Gemini", "gemini")
        self.provider_combo.addItem("OpenAI-Compatible (OpenAI / OpenRouter / Groq / Ollama)", "openai_compatible")
        p_idx = self.provider_combo.findData(self._settings.llm_provider)
        self.provider_combo.setCurrentIndex(max(0, p_idx))
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_form.addRow("Provider AI", self.provider_combo)
        ai_lay.addLayout(provider_form)

        self.provider_stack = QStackedWidget()

        gemini_page = QWidget()
        gemini_form = QFormLayout(gemini_page)
        gemini_form.setContentsMargins(0, 0, 0, 0)
        gemini_form.setSpacing(10)

        g_key_row = QHBoxLayout()
        g_key_row.setSpacing(8)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Masukkan GEMINI_API_KEY...")
        self.api_key_edit.setText(self._settings.gemini_api_key)
        self.toggle_key_btn = QPushButton("Tampilkan")
        self.toggle_key_btn.setCheckable(True)
        self.toggle_key_btn.toggled.connect(self._toggle_gemini_key_visibility)
        g_key_row.addWidget(self.api_key_edit, 1)
        g_key_row.addWidget(self.toggle_key_btn)
        gemini_form.addRow("Gemini API Key", g_key_row)

        self.gemini_model_combo = QComboBox()
        self.gemini_model_combo.setEditable(True)
        self.gemini_model_combo.addItems(["gemini-4-neon", "gemini-2.5-flash", "gemini-2.5-pro"])
        self.gemini_model_combo.setCurrentText(self._settings.gemini_model or "gemini-4-neon")
        gemini_form.addRow("Gemini Model", self.gemini_model_combo)

        openai_page = QWidget()
        openai_form = QFormLayout(openai_page)
        openai_form.setContentsMargins(0, 0, 0, 0)
        openai_form.setSpacing(10)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Custom")
        for preset_name in _OPENAI_PRESETS:
            self.preset_combo.addItem(preset_name)
        openai_form.addRow("Preset Endpoint", self.preset_combo)

        self.openai_url_edit = QLineEdit(self._settings.openai_base_url)
        self.openai_url_edit.setPlaceholderText("https://api.openai.com/v1")
        openai_form.addRow("Base URL", self.openai_url_edit)

        o_key_row = QHBoxLayout()
        o_key_row.setSpacing(8)
        self.openai_key_edit = QLineEdit()
        self.openai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_edit.setPlaceholderText("Masukkan API Key (opsional untuk Ollama/LM Studio lokal)")
        self.openai_key_edit.setText(self._settings.openai_api_key)
        self.toggle_openai_key_btn = QPushButton("Tampilkan")
        self.toggle_openai_key_btn.setCheckable(True)
        self.toggle_openai_key_btn.toggled.connect(self._toggle_openai_key_visibility)
        o_key_row.addWidget(self.openai_key_edit, 1)
        o_key_row.addWidget(self.toggle_openai_key_btn)
        openai_form.addRow("API Key", o_key_row)

        self.openai_model_edit = QLineEdit(self._settings.openai_model)
        self.openai_model_edit.setPlaceholderText("gpt-4o-mini / openai/gpt-4o-mini / qwen2.5:7b")
        openai_form.addRow("Model Name", self.openai_model_edit)

        self._select_matching_preset(self._settings.openai_base_url)
        self.preset_combo.currentTextChanged.connect(self._on_preset_selected)

        self.provider_stack.addWidget(gemini_page)
        self.provider_stack.addWidget(openai_page)
        ai_lay.addWidget(self.provider_stack)
        body.addWidget(ai_card)

        # ---- Card 2: Whisper & Proofreading ----
        tr_card = QFrame()
        tr_card.setObjectName("Card")
        tr_lay = QVBoxLayout(tr_card)
        tr_lay.setContentsMargins(16, 14, 16, 14)
        tr_lay.setSpacing(10)

        tr_title = QLabel("2. Transkripsi Whisper & Koreksi Anti-Typo")
        tr_title.setObjectName("SectionTitle")
        tr_lay.addWidget(tr_title)

        tr_form = QFormLayout()
        tr_form.setSpacing(10)
        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.addItems(["tiny", "base", "small", "medium", "large-v3-turbo"])
        self.whisper_model_combo.setCurrentText(self._settings.whisper_model)
        tr_form.addRow("Whisper Model", self.whisper_model_combo)

        self.whisper_device_combo = QComboBox()
        self.whisper_device_combo.addItems(["auto", "cpu", "cuda"])
        self.whisper_device_combo.setCurrentText(self._settings.whisper_device)
        tr_form.addRow("Whisper Device", self.whisper_device_combo)

        self.whisper_lang_combo = QComboBox()
        self.whisper_lang_combo.addItem("Otomatis (Sesuai Judul & Audio)", "auto")
        self.whisper_lang_combo.addItem("Bahasa Indonesia (id)", "id")
        self.whisper_lang_combo.addItem("English (en)", "en")
        wl_idx = self.whisper_lang_combo.findData(self._settings.whisper_language)
        self.whisper_lang_combo.setCurrentIndex(max(0, wl_idx))
        tr_form.addRow("Bahasa Transkrip", self.whisper_lang_combo)

        self.proofread_chk = ToggleSwitch(
            "Perbaiki typo & ejaan transkrip otomatis (kamus lokal + AI)",
            checked=bool(self._settings.enable_transcript_proofread),
            accent_color="#7C3AED",
        )
        tr_form.addRow("Koreksi Typo", self.proofread_chk)
        tr_lay.addLayout(tr_form)
        body.addWidget(tr_card)

        # ---- Card 3: Workspace, Backsound & TTS ----
        media_card = QFrame()
        media_card.setObjectName("Card")
        media_lay = QVBoxLayout(media_card)
        media_lay.setContentsMargins(16, 14, 16, 14)
        media_lay.setSpacing(10)

        media_title = QLabel("3. Workspace, Backsound & Suara Opening (TTS)")
        media_title.setObjectName("SectionTitle")
        media_lay.addWidget(media_title)

        media_form = QFormLayout()
        media_form.setSpacing(10)

        ws_row = QHBoxLayout()
        ws_row.setSpacing(8)
        self.workspace_edit = QLineEdit(str(self._settings.workspace_dir))
        browse_btn = QPushButton("Pilih...")
        browse_btn.clicked.connect(self._browse_workspace)
        ws_row.addWidget(self.workspace_edit, 1)
        ws_row.addWidget(browse_btn)
        media_form.addRow("Workspace Folder", ws_row)

        bgm_row = QHBoxLayout()
        bgm_row.setSpacing(8)
        self.backsound_edit = QLineEdit(str(self._settings.backsound_dir))
        browse_bgm_btn = QPushButton("Pilih...")
        browse_bgm_btn.clicked.connect(self._browse_backsound_dir)
        open_bgm_btn = QPushButton("Buka")
        open_bgm_btn.clicked.connect(self._open_backsound_dir)
        bgm_row.addWidget(self.backsound_edit, 1)
        bgm_row.addWidget(browse_bgm_btn)
        bgm_row.addWidget(open_bgm_btn)
        media_form.addRow("Folder Backsound", bgm_row)

        bgm_opts_row = QHBoxLayout()
        bgm_opts_row.setSpacing(12)
        self.bgm_volume_spin = QSpinBox()
        self.bgm_volume_spin.setRange(0, 100)
        self.bgm_volume_spin.setSuffix(" %")
        self.bgm_volume_spin.setValue(int(round(self._settings.backsound_volume * 100)))

        self.bgm_ducking_chk = ToggleSwitch(
            "Auto-Ducking (musik meredup saat ada percakapan)",
            checked=bool(self._settings.enable_backsound_ducking),
            accent_color="#06B6D4",
        )
        bgm_opts_row.addWidget(self.bgm_volume_spin)
        bgm_opts_row.addWidget(self.bgm_ducking_chk, 1)
        media_form.addRow("Volume Backsound", bgm_opts_row)

        self.tts_voice_combo = QComboBox()
        for label, voice_id in TTS_VOICE_CHOICES:
            self.tts_voice_combo.addItem(label, voice_id)
        tts_idx = self.tts_voice_combo.findData(self._settings.tts_voice)
        self.tts_voice_combo.setCurrentIndex(max(0, tts_idx))
        media_form.addRow("Suara Opening (TTS)", self.tts_voice_combo)

        tts_style_row = QHBoxLayout()
        tts_style_row.setSpacing(8)
        self.tts_style_combo = QComboBox()
        for style_id, (style_label, _r, _p) in TTS_STYLE_PRESETS.items():
            self.tts_style_combo.addItem(style_label, style_id)
        st_idx = self.tts_style_combo.findData(getattr(self._settings, "tts_style", "energetic"))
        self.tts_style_combo.setCurrentIndex(max(0, st_idx))
        self.tts_test_btn = QPushButton("Coba Suara")
        self.tts_test_btn.setObjectName("SmallAction")
        self.tts_test_btn.clicked.connect(self._preview_tts_voice)
        tts_style_row.addWidget(self.tts_style_combo, 1)
        tts_style_row.addWidget(self.tts_test_btn)
        media_form.addRow("Gaya Pembaca (TTS)", tts_style_row)

        self.tts_test_lbl = QLabel("")
        self.tts_test_lbl.setObjectName("MutedText")
        media_form.addRow("", self.tts_test_lbl)

        media_lay.addLayout(media_form)
        body.addWidget(media_card)
        body.addStretch(1)

        scroll.setWidget(scroll_body)
        outer.addWidget(scroll, 1)

        # Sticky Save Bar
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        if not embedded:
            cancel_btn = QPushButton("Batal")
            cancel_btn.clicked.connect(self.cancelled.emit)
            btn_row.addWidget(cancel_btn)
        self.save_btn = QPushButton("Simpan Pengaturan")
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.save_btn)
        outer.addLayout(btn_row)

        self._on_provider_changed()

    def load_settings(self, settings: AppSettings) -> None:
        self._settings = settings.model_copy(deep=True)
        p_idx = self.provider_combo.findData(self._settings.llm_provider)
        self.provider_combo.setCurrentIndex(max(0, p_idx))
        self.api_key_edit.setText(self._settings.gemini_api_key)
        self.gemini_model_combo.setCurrentText(self._settings.gemini_model or "gemini-4-neon")
        self.openai_url_edit.setText(self._settings.openai_base_url)
        self.openai_key_edit.setText(self._settings.openai_api_key)
        self.openai_model_edit.setText(self._settings.openai_model)
        self._select_matching_preset(self._settings.openai_base_url)
        self.whisper_model_combo.setCurrentText(self._settings.whisper_model)
        self.whisper_device_combo.setCurrentText(self._settings.whisper_device)
        wl_idx = self.whisper_lang_combo.findData(self._settings.whisper_language)
        self.whisper_lang_combo.setCurrentIndex(max(0, wl_idx))
        self.proofread_chk.setChecked(bool(self._settings.enable_transcript_proofread))
        self.workspace_edit.setText(str(self._settings.workspace_dir))
        self.backsound_edit.setText(str(self._settings.backsound_dir))
        self.bgm_volume_spin.setValue(int(round(self._settings.backsound_volume * 100)))
        self.bgm_ducking_chk.setChecked(bool(self._settings.enable_backsound_ducking))
        tts_idx = self.tts_voice_combo.findData(self._settings.tts_voice)
        self.tts_voice_combo.setCurrentIndex(max(0, tts_idx))
        st_idx = self.tts_style_combo.findData(getattr(self._settings, "tts_style", "energetic"))
        self.tts_style_combo.setCurrentIndex(max(0, st_idx))
        self._on_provider_changed()

    def _select_matching_preset(self, base_url: str) -> None:
        normalized = base_url.strip().rstrip("/").lower()
        self._suppress_preset_fill = True
        try:
            for name, (preset_url, _) in _OPENAI_PRESETS.items():
                if normalized == preset_url.rstrip("/").lower():
                    self.preset_combo.setCurrentText(name)
                    return
            self.preset_combo.setCurrentText("Custom")
        finally:
            self._suppress_preset_fill = False

    def _on_preset_selected(self, preset_name: str) -> None:
        if self._suppress_preset_fill:
            return
        preset = _OPENAI_PRESETS.get(preset_name)
        if preset is None:
            return
        base_url, sample_model = preset
        self.openai_url_edit.setText(base_url)
        self.openai_model_edit.setText(sample_model)

    def _on_provider_changed(self) -> None:
        provider = self.provider_combo.currentData() or "gemini"
        is_openai = provider == "openai_compatible"
        self.provider_stack.setCurrentIndex(1 if is_openai else 0)
        for idx in range(self.provider_stack.count()):
            page = self.provider_stack.widget(idx)
            if page is not None:
                page.setVisible(idx == (1 if is_openai else 0))

    def _toggle_gemini_key_visibility(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.toggle_key_btn.setText("Sembunyikan" if checked else "Tampilkan")

    def _toggle_openai_key_visibility(self, checked: bool) -> None:
        self.openai_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self.toggle_openai_key_btn.setText("Sembunyikan" if checked else "Tampilkan")

    def _browse_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Pilih Workspace Folder", self.workspace_edit.text() or str(Path.cwd())
        )
        if selected:
            self.workspace_edit.setText(selected)

    def _browse_backsound_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Pilih Folder Backsound", self.backsound_edit.text() or str(Path.cwd())
        )
        if selected:
            self.backsound_edit.setText(selected)

    def _open_backsound_dir(self) -> None:
        folder = Path(self.backsound_edit.text().strip() or str(self._settings.backsound_dir))
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def collect_settings(self) -> Optional[AppSettings]:
        provider = self.provider_combo.currentData() or "gemini"
        gemini_model = self.gemini_model_combo.currentText().strip() or "gemini-4-neon"
        openai_url = self.openai_url_edit.text().strip()
        openai_model = self.openai_model_edit.text().strip()

        if provider == "openai_compatible":
            if not (openai_url.startswith("http://") or openai_url.startswith("https://")):
                QMessageBox.warning(
                    self,
                    "Base URL Tidak Valid",
                    "Base URL OpenAI-Compatible harus diawali http:// atau https://.",
                )
                return None
            if not openai_model:
                QMessageBox.warning(
                    self,
                    "Model Name Kosong",
                    "Masukkan nama model untuk provider OpenAI-Compatible (mis. gpt-4o-mini).",
                )
                return None

        self._settings.llm_provider = provider  # type: ignore[assignment]
        self._settings.gemini_api_key = self.api_key_edit.text().strip()
        self._settings.gemini_model = gemini_model
        self._settings.openai_base_url = openai_url or self._settings.openai_base_url
        self._settings.openai_api_key = self.openai_key_edit.text().strip()
        self._settings.openai_model = openai_model or self._settings.openai_model
        self._settings.whisper_model = self.whisper_model_combo.currentText()  # type: ignore[assignment]
        self._settings.whisper_device = self.whisper_device_combo.currentText()  # type: ignore[assignment]
        self._settings.whisper_language = self.whisper_lang_combo.currentData() or "auto"  # type: ignore[assignment]
        self._settings.enable_transcript_proofread = self.proofread_chk.isChecked()
        ws_text = self.workspace_edit.text().strip()
        if ws_text:
            self._settings.workspace_dir = Path(ws_text)
        bgm_text = self.backsound_edit.text().strip()
        if bgm_text:
            self._settings.backsound_dir = Path(bgm_text)
        self._settings.backsound_volume = round(self.bgm_volume_spin.value() / 100.0, 2)
        self._settings.enable_backsound_ducking = self.bgm_ducking_chk.isChecked()
        self._settings.tts_voice = self.tts_voice_combo.currentData() or "id-ID-GadisNeural"
        tts_style = str(self.tts_style_combo.currentData() or "energetic")
        self._settings.tts_style = tts_style  # type: ignore[assignment]
        self._settings.tts_rate, self._settings.tts_pitch = resolve_tts_prosody(tts_style)
        return self._settings

    def _preview_tts_voice(self) -> None:
        import asyncio
        import tempfile
        from atro.core.logging_utils import JobLogger
        from atro.core.preflight import resolve_binary

        voice = str(self.tts_voice_combo.currentData() or "id-ID-GadisNeural")
        style = str(self.tts_style_combo.currentData() or "energetic")
        rate, pitch = resolve_tts_prosody(style)
        sample_text = (
            "Whoa, you won't believe what happened at the end!"
            if voice.startswith("en-")
            else "Wajib tonton! Momen paling gila di video ini!"
        )
        self.tts_test_btn.setEnabled(False)
        self.tts_test_btn.setText("Membuat...")
        self.tts_test_lbl.setText("Menyiapkan contoh suara...")
        tmp_base = Path(tempfile.gettempdir()) / "atro_settings_tts_preview"
        try:
            ffprobe_p = resolve_binary("ffprobe", self._settings.bin_dir)
        except Exception:
            ffprobe_p = None

        def _worker() -> None:
            try:
                logger = JobLogger("tts_settings_preview", Path(tempfile.gettempdir()) / "atro_studio_tts.log")
                out_p, dur = asyncio.run(
                    synthesize_opening_voiceover(
                        text=sample_text,
                        output_audio_path=tmp_base,
                        voice=voice,
                        rate=rate,
                        ffprobe_path=ffprobe_p,
                        logger=logger,
                        pitch=pitch,
                    )
                )
                self._tts_preview_ready.emit(str(out_p) if out_p else "", float(dur), "")
            except Exception as exc:
                self._tts_preview_ready.emit("", 0.0, str(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_tts_preview_ready(self, audio_path_str: str, dur: float, err_msg: str) -> None:
        self.tts_test_btn.setEnabled(True)
        self.tts_test_btn.setText("Coba Suara")
        if err_msg or not audio_path_str or not Path(audio_path_str).is_file():
            self.tts_test_lbl.setText(f"Gagal memutar suara: {err_msg or 'TTS tidak tersedia.'}")
            return
        self.tts_test_lbl.setText(f"Memutar contoh suara ({dur:.1f} dtk)...")
        play_audio_preview_file(self, audio_path_str)

    def _on_save(self) -> None:
        updated = self.collect_settings()
        if updated is not None:
            self.settings_saved.emit(updated)

    def updated_settings(self) -> AppSettings:
        return self._settings


class SettingsDialog(QDialog):
    """Modal compatibility wrapper around SettingsWidget."""

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pengaturan ATRO Creator Studio")
        self.setMinimumSize(640, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.widget = SettingsWidget(settings, embedded=False, parent=self)
        self.widget.settings_saved.connect(self._on_widget_saved)
        self.widget.cancelled.connect(self.reject)
        root.addWidget(self.widget)

    def _on_widget_saved(self, _updated: AppSettings) -> None:
        self.accept()

    def updated_settings(self) -> AppSettings:
        return self.widget.updated_settings()

    def __getattr__(self, name: str):
        return getattr(self.widget, name)
