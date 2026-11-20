"""Flat modern settings dialog for AI provider (Gemini / OpenAI-Compatible), Whisper, and workspace."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from atro.config import AppSettings

_OPENAI_PRESETS: dict[str, tuple[str, str]] = {
    "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "OpenRouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "Groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "Ollama (Lokal)": ("http://localhost:11434/v1", "qwen2.5:7b"),
    "LM Studio (Lokal)": ("http://localhost:1234/v1", "local-model"),
}


class SettingsDialog(QDialog):
    """Modal dialog for editing persistent ATRO configuration."""

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = settings.model_copy(deep=True)
        self._suppress_preset_fill = False

        self.setWindowTitle("Pengaturan ATRO")
        self.setMinimumWidth(520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Pengaturan Aplikasi")
        title.setObjectName("SectionTitle")
        subtitle = QLabel(
            "Pilih provider AI (Google Gemini atau OpenAI-Compatible), model Whisper lokal, dan lokasi workspace."
        )
        subtitle.setObjectName("MutedText")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # Provider selector
        provider_form = QFormLayout()
        provider_form.setSpacing(10)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Google Gemini", "gemini")
        self.provider_combo.addItem("OpenAI-Compatible", "openai_compatible")
        idx = self.provider_combo.findData(self._settings.llm_provider)
        self.provider_combo.setCurrentIndex(max(0, idx))
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_form.addRow("Provider AI", self.provider_combo)
        layout.addLayout(provider_form)

        # Provider-specific fields stack
        self.provider_stack = QStackedWidget()

        # Page 0: Google Gemini
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

        # Page 1: OpenAI-Compatible
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
        self.openai_key_edit.setPlaceholderText(
            "Masukkan API Key (opsional untuk Ollama/LM Studio lokal)"
        )
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
        layout.addWidget(self.provider_stack)

        # Common Whisper + Workspace fields
        common_form = QFormLayout()
        common_form.setSpacing(10)

        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.addItems(["tiny", "base", "small", "medium", "large-v3-turbo"])
        self.whisper_model_combo.setCurrentText(self._settings.whisper_model)
        common_form.addRow("Whisper Model", self.whisper_model_combo)

        self.whisper_device_combo = QComboBox()
        self.whisper_device_combo.addItems(["auto", "cpu", "cuda"])
        self.whisper_device_combo.setCurrentText(self._settings.whisper_device)
        common_form.addRow("Whisper Device", self.whisper_device_combo)

        ws_row = QHBoxLayout()
        ws_row.setSpacing(8)
        self.workspace_edit = QLineEdit(str(self._settings.workspace_dir))
        browse_btn = QPushButton("Pilih...")
        browse_btn.clicked.connect(self._browse_workspace)
        ws_row.addWidget(self.workspace_edit, 1)
        ws_row.addWidget(browse_btn)
        common_form.addRow("Workspace Folder", ws_row)

        # Backsound folder row
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
        common_form.addRow("Folder Backsound", bgm_row)

        # Backsound volume + ducking row
        bgm_opts_row = QHBoxLayout()
        bgm_opts_row.setSpacing(12)
        self.bgm_volume_spin = QSpinBox()
        self.bgm_volume_spin.setRange(0, 100)
        self.bgm_volume_spin.setSuffix(" %")
        self.bgm_volume_spin.setValue(int(round(self._settings.backsound_volume * 100)))

        self.bgm_ducking_chk = QCheckBox("Auto-Ducking (musik meredup saat ada percakapan)")
        self.bgm_ducking_chk.setChecked(self._settings.enable_backsound_ducking)

        bgm_opts_row.addWidget(self.bgm_volume_spin)
        bgm_opts_row.addWidget(self.bgm_ducking_chk, 1)
        common_form.addRow("Volume Backsound", bgm_opts_row)

        layout.addLayout(common_form)

        # Sync initial stack page and compact height
        self._on_provider_changed()

        # Footer buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Batal")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Simpan Pengaturan")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

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
        provider = self.provider_combo.currentData()
        page_idx = 1 if provider == "openai_compatible" else 0
        self.provider_stack.setCurrentIndex(page_idx)
        current = self.provider_stack.currentWidget()
        if current is not None:
            self.provider_stack.setFixedHeight(max(70, current.sizeHint().height()))
        self.adjustSize()

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
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Pilih Folder Workspace",
            self.workspace_edit.text() or str(self._settings.workspace_dir),
        )
        if chosen:
            self.workspace_edit.setText(chosen)

    def _browse_backsound_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Pilih Folder Backsound",
            self.backsound_edit.text() or str(self._settings.backsound_dir),
        )
        if chosen:
            self.backsound_edit.setText(chosen)

    def _open_backsound_dir(self) -> None:
        raw = self.backsound_edit.text().strip()
        target = Path(raw) if raw else self._settings.backsound_dir
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target)])

    def _on_save(self) -> None:
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
                return
            if not openai_model:
                QMessageBox.warning(
                    self,
                    "Model Name Kosong",
                    "Masukkan nama model untuk provider OpenAI-Compatible (mis. gpt-4o-mini).",
                )
                return

        self._settings.llm_provider = provider  # type: ignore[assignment]
        self._settings.gemini_api_key = self.api_key_edit.text().strip()
        self._settings.gemini_model = gemini_model
        self._settings.openai_base_url = openai_url or self._settings.openai_base_url
        self._settings.openai_api_key = self.openai_key_edit.text().strip()
        self._settings.openai_model = openai_model or self._settings.openai_model
        self._settings.whisper_model = self.whisper_model_combo.currentText()  # type: ignore[assignment]
        self._settings.whisper_device = self.whisper_device_combo.currentText()  # type: ignore[assignment]
        ws_text = self.workspace_edit.text().strip()
        if ws_text:
            self._settings.workspace_dir = Path(ws_text)
        bgm_text = self.backsound_edit.text().strip()
        if bgm_text:
            self._settings.backsound_dir = Path(bgm_text)
        self._settings.backsound_volume = round(self.bgm_volume_spin.value() / 100.0, 2)
        self._settings.enable_backsound_ducking = self.bgm_ducking_chk.isChecked()
        self.accept()

    def updated_settings(self) -> AppSettings:
        return self._settings
