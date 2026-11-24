"""Collapsible Dark Creator Studio left sidebar navigation rail."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from atro.ui.components import create_vector_icon

NAV_ITEMS: tuple[tuple[int, str, str, str], ...] = (
    (0, "Home", "home", "Quick Clip: paste URL YouTube atau drag & drop video"),
    (1, "Templates", "templates", "Galeri template 1-klik siap pakai & favorit"),
    (2, "Editor", "editor", "3-Panel Editor Studio, Live Preview 9:16 & Multi-Track Timeline"),
    (3, "Render Queue", "queue", "Antrean proses, log, dan hasil klip OpusClip Feed"),
    (4, "Settings", "settings", "AI Provider, Whisper Anti-Typo, Backsound & TTS"),
)


class CreatorSidebarWidget(QFrame):
    """Left collapsible navigation rail for ATRO Dark Creator Studio."""

    page_requested = pyqtSignal(int)
    demo_requested = pyqtSignal()
    theme_toggle_requested = pyqtSignal()
    preflight_clicked = pyqtSignal()
    collapsed_changed = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self._expanded_width = 192
        self._collapsed_width = 64
        self._collapsed = False
        self._current_page = 0
        self._theme_mode = "dark"
        self._queue_badge = ""
        self._nav_buttons: dict[int, QPushButton] = {}
        self._nav_labels: dict[int, str] = {}

        self.setFixedWidth(self._expanded_width)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 14, 10, 12)
        root.setSpacing(6)

        # Header / Brand Row
        hdr = QHBoxLayout()
        hdr.setContentsMargins(2, 0, 2, 6)
        hdr.setSpacing(8)

        self.brand_badge = QLabel("A")
        self.brand_badge.setFixedSize(30, 30)
        self.brand_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_badge.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #7C3AED, stop:1 #06B6D4);"
            "color: #FFFFFF; font-weight: 900; font-size: 14px; border-radius: 8px;"
        )
        hdr.addWidget(self.brand_badge)

        brand_text_col = QVBoxLayout()
        brand_text_col.setContentsMargins(0, 0, 0, 0)
        brand_text_col.setSpacing(0)
        self.brand_title = QLabel("ATRO")
        self.brand_title.setObjectName("BrandTitle")
        self.brand_sub = QLabel("Creator Studio")
        self.brand_sub.setObjectName("MutedText")
        self.brand_sub.setStyleSheet("font-size: 10px; font-weight: 600;")
        brand_text_col.addWidget(self.brand_title)
        brand_text_col.addWidget(self.brand_sub)
        self.brand_text_wrap = QWidget()
        self.brand_text_wrap.setLayout(brand_text_col)
        hdr.addWidget(self.brand_text_wrap, 1)

        self.collapse_btn = QPushButton()
        self.collapse_btn.setObjectName("SidebarCollapseBtn")
        self.collapse_btn.setFixedSize(28, 28)
        self.collapse_btn.setIcon(create_vector_icon("chevron_left", "#9CA3AF", 15))
        self.collapse_btn.setToolTip("Ciutkan / lebarkan sidebar")
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        hdr.addWidget(self.collapse_btn)

        root.addLayout(hdr)

        self.nav_section_lbl = QLabel("WORKSPACE")
        self.nav_section_lbl.setObjectName("SidebarSectionLabel")
        root.addWidget(self.nav_section_lbl)

        # 5 Main Menu Items
        for idx, label, icon_name, tooltip in NAV_ITEMS:
            btn = QPushButton(f"  {label}")
            btn.setObjectName("SidebarNavBtn")
            btn.setCheckable(True)
            btn.setToolTip(tooltip)
            btn.setIconSize(QSize(18, 18))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, page=idx: self._on_nav_clicked(page))
            self._nav_buttons[idx] = btn
            self._nav_labels[idx] = label
            root.addWidget(btn)

        root.addStretch(1)

        # Utility Footer
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background-color: #242836;")
        root.addWidget(div)
        root.addSpacing(4)

        self.health_btn = QPushButton("Cek Sistem...")
        self.health_btn.setObjectName("SmallAction")
        self.health_btn.setIcon(create_vector_icon("check", "#10B981", 15))
        self.health_btn.setToolTip("Status dependency (FFmpeg / yt-dlp / API Key / Whisper)")
        self.health_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.health_btn.clicked.connect(self.preflight_clicked.emit)
        root.addWidget(self.health_btn)

        self.demo_btn = QPushButton("Demo Offline")
        self.demo_btn.setObjectName("SmallAction")
        self.demo_btn.setIcon(create_vector_icon("sparkles", "#06B6D4", 15))
        self.demo_btn.setToolTip("Buat video sample lokal offline untuk uji cepat Editor Studio")
        self.demo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.demo_btn.clicked.connect(self.demo_requested.emit)
        root.addWidget(self.demo_btn)

        self.theme_btn = QPushButton("Tema: Dark")
        self.theme_btn.setObjectName("GhostButton")
        self.theme_btn.setToolTip("Ganti tema Dark Creator Studio / Light")
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.clicked.connect(self.theme_toggle_requested.emit)
        root.addWidget(self.theme_btn)

        self.set_active_page(0)

    def _on_nav_clicked(self, page: int) -> None:
        self.set_active_page(page)
        self.page_requested.emit(page)

    def set_active_page(self, page: int) -> None:
        self._current_page = page
        icon_map = {idx: icon for idx, _, icon, _ in NAV_ITEMS}
        for idx, btn in self._nav_buttons.items():
            active = idx == page
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            btn.setProperty("active", "true" if active else "false")
            icon_color = "#FFFFFF" if active else ("#9CA3AF" if self._theme_mode == "dark" else "#64748B")
            btn.setIcon(create_vector_icon(icon_map[idx], icon_color, 18))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_queue_badge(self, text: str) -> None:
        self._queue_badge = text.strip()
        self._refresh_nav_texts()

    def set_health_status(self, text: str, ok: bool) -> None:
        color = "#10B981" if ok else "#F59E0B"
        self.health_btn.setIcon(create_vector_icon("check" if ok else "refresh", color, 15))
        self.health_btn.setToolTip(f"Status Sistem: {text}")
        if not self._collapsed:
            self.health_btn.setText(f" {text}")

    def set_theme_label(self, mode: str) -> None:
        self._theme_mode = mode
        if not self._collapsed:
            self.theme_btn.setText("Tema: Dark" if mode == "dark" else "Tema: Light")
        self.set_active_page(self._current_page)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.setFixedWidth(self._collapsed_width if collapsed else self._expanded_width)
        self.brand_text_wrap.setVisible(not collapsed)
        self.nav_section_lbl.setVisible(not collapsed)
        self.collapse_btn.setIcon(
            create_vector_icon("chevron_right" if collapsed else "chevron_left", "#9CA3AF", 15)
        )
        self._refresh_nav_texts()
        self.collapsed_changed.emit(self._collapsed)

    def _refresh_nav_texts(self) -> None:
        for idx, btn in self._nav_buttons.items():
            if self._collapsed:
                btn.setText("")
            else:
                lbl = self._nav_labels[idx]
                if idx == 3 and self._queue_badge:
                    lbl = f"{lbl} ({self._queue_badge})"
                btn.setText(f"  {lbl}")
        if self._collapsed:
            self.health_btn.setText("")
            self.demo_btn.setText("")
            self.theme_btn.setText("☀" if self._theme_mode == "dark" else "☾")
        else:
            self.demo_btn.setText(" Demo Offline")
            self.theme_btn.setText("Tema: Dark" if self._theme_mode == "dark" else "Tema: Light")
