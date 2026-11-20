"""Flat Modern Dark Slate QSS stylesheet and design tokens for ATRO."""

from __future__ import annotations

PALETTE = {
    "bg_base": "#0F1117",
    "bg_surface": "#161A23",
    "bg_elevated": "#1C2230",
    "bg_hover": "#232A3B",
    "border": "#262D3F",
    "border_focus": "#3B82F6",
    "text_primary": "#F1F5F9",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",
    "accent": "#3B82F6",
    "accent_hover": "#2563EB",
    "accent_pressed": "#1D4ED8",
    "success": "#10B981",
    "warning": "#F59E0B",
    "error": "#EF4444",
}


def build_stylesheet() -> str:
    """Return crisp flat modern QSS stylesheet without gradients or heavy shadows."""
    return """
    QWidget {
        background-color: #0F1117;
        color: #F1F5F9;
        font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
        font-size: 13px;
    }

    QMainWindow, QDialog {
        background-color: #0F1117;
    }

    /* Flat Card Surfaces */
    QFrame#Card, QFrame#TopBar, QFrame#JobCard, QFrame#ClipCard {
        background-color: #161A23;
        border: 1px solid #262D3F;
        border-radius: 10px;
    }

    QFrame#JobCard[selected="true"] {
        background-color: #1A202E;
        border: 1px solid #3B82F6;
    }

    QFrame#JobCard:hover {
        background-color: #1A202C;
        border: 1px solid #334155;
    }

    QFrame#JobCard[selected="true"]:hover {
        border: 1px solid #3B82F6;
    }

    QFrame#AlertBanner[severity="error"] {
        background-color: #26161B;
        border: 1px solid #7F1D1D;
        border-radius: 8px;
    }

    QFrame#AlertBanner[severity="warning"] {
        background-color: #261E12;
        border: 1px solid #78350F;
        border-radius: 8px;
    }

    QFrame#AlertBanner QLabel {
        background: transparent;
    }

    /* Typography Helpers */
    QLabel {
        background-color: transparent;
    }

    QLabel#BrandTitle {
        font-size: 19px;
        font-weight: 700;
        letter-spacing: 1px;
        color: #F8FAFC;
    }

    QLabel#SectionTitle {
        font-size: 14px;
        font-weight: 600;
        color: #F1F5F9;
    }

    QLabel#MutedText {
        color: #94A3B8;
        font-size: 12px;
    }

    QLabel#FieldLabel {
        color: #94A3B8;
        font-size: 12px;
        font-weight: 500;
    }

    QLabel#VersionPill {
        background-color: #1C2230;
        color: #94A3B8;
        border: 1px solid #262D3F;
        border-radius: 6px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
    }

    /* Status & Preflight Pills */
    QLabel#StatusPill {
        border-radius: 6px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 600;
    }

    QLabel#StatusPill[tone="ok"] {
        background-color: #102A23;
        color: #34D399;
        border: 1px solid #15493B;
    }

    QLabel#StatusPill[tone="warn"] {
        background-color: #292011;
        color: #FBBF24;
        border: 1px solid #594115;
    }

    QLabel#StatusPill[tone="error"] {
        background-color: #2B1519;
        color: #F87171;
        border: 1px solid #5F2128;
    }

    QLabel#StatusPill[tone="info"] {
        background-color: #162238;
        color: #60A5FA;
        border: 1px solid #233B66;
    }

    QLabel#StatusPill[tone="neutral"] {
        background-color: #1C2230;
        color: #94A3B8;
        border: 1px solid #2A3347;
    }

    /* Inputs */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: #0F1117;
        color: #F1F5F9;
        border: 1px solid #262D3F;
        border-radius: 8px;
        padding: 7px 10px;
        min-height: 20px;
        selection-background-color: #3B82F6;
    }

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
        border: 1px solid #3B82F6;
    }

    QLineEdit::placeholder {
        color: #64748B;
    }

    QComboBox::drop-down {
        border: none;
        width: 24px;
    }

    QComboBox QAbstractItemView {
        background-color: #161A23;
        color: #F1F5F9;
        border: 1px solid #262D3F;
        selection-background-color: #232A3B;
        outline: 0;
    }

    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
        width: 18px;
        border: none;
        background: transparent;
    }

    /* Flat Buttons */
    QPushButton {
        background-color: #1C2230;
        color: #F1F5F9;
        border: 1px solid #2A3347;
        border-radius: 8px;
        padding: 7px 14px;
        font-weight: 500;
    }

    QPushButton:hover {
        background-color: #232A3B;
        border: 1px solid #38445E;
    }

    QPushButton:pressed {
        background-color: #191E2A;
    }

    QPushButton:disabled {
        background-color: #141720;
        color: #475569;
        border: 1px solid #1E2433;
    }

    QPushButton#PrimaryButton {
        background-color: #3B82F6;
        color: #FFFFFF;
        border: 1px solid #3B82F6;
        font-weight: 600;
        padding: 8px 18px;
    }

    QPushButton#PrimaryButton:hover {
        background-color: #2563EB;
        border: 1px solid #2563EB;
    }

    QPushButton#PrimaryButton:pressed {
        background-color: #1D4ED8;
        border: 1px solid #1D4ED8;
    }

    QPushButton#PrimaryButton:disabled {
        background-color: #1E3A63;
        color: #64748B;
        border: 1px solid #1E3A63;
    }

    QPushButton#DangerButton {
        background-color: #2B1519;
        color: #F87171;
        border: 1px solid #5F2128;
    }

    QPushButton#DangerButton:hover {
        background-color: #3B1A20;
    }

    QPushButton#SmallAction {
        padding: 4px 10px;
        font-size: 12px;
        border-radius: 6px;
    }

    QPushButton#ModeSwitch {
        background-color: #11141C;
        color: #94A3B8;
        border: 1px solid #262D3F;
        border-radius: 7px;
        padding: 5px 14px;
        font-size: 12px;
        font-weight: 500;
    }

    QPushButton#ModeSwitch:hover {
        color: #E2E8F0;
        border: 1px solid #334155;
    }

    QPushButton#ModeSwitch:checked {
        background-color: #162238;
        color: #60A5FA;
        border: 1px solid #3B82F6;
        font-weight: 600;
    }

    /* Progress Bar */
    QProgressBar {
        background-color: #0F1117;
        border: none;
        border-radius: 3px;
        min-height: 6px;
        max-height: 6px;
        text-align: center;
    }

    QProgressBar::chunk {
        background-color: #3B82F6;
        border-radius: 3px;
    }

    QProgressBar[tone="ok"]::chunk {
        background-color: #10B981;
    }

    QProgressBar[tone="error"]::chunk {
        background-color: #EF4444;
    }

    QProgressBar[tone="warn"]::chunk {
        background-color: #F59E0B;
    }

    /* Flat Checkboxes */
    QCheckBox {
        spacing: 7px;
        color: #CBD5E1;
        font-size: 12px;
        background-color: transparent;
    }

    QCheckBox::indicator {
        width: 15px;
        height: 15px;
        border-radius: 4px;
        border: 1px solid #334155;
        background-color: #0F1117;
    }

    QCheckBox::indicator:hover {
        border: 1px solid #475569;
    }

    QCheckBox::indicator:checked {
        background-color: #3B82F6;
        border: 1px solid #3B82F6;
    }

    /* Flat Tabs */
    QTabWidget::pane {
        border: none;
        border-top: 1px solid #262D3F;
        background-color: transparent;
        padding-top: 10px;
    }

    QTabBar {
        background-color: transparent;
    }

    QTabBar::tab {
        background-color: transparent;
        color: #94A3B8;
        padding: 8px 16px;
        margin-right: 4px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 500;
    }

    QTabBar::tab:hover {
        color: #E2E8F0;
    }

    QTabBar::tab:selected {
        color: #F8FAFC;
        border-bottom: 2px solid #3B82F6;
        font-weight: 600;
    }

    /* Log Viewer */
    QPlainTextEdit#LogEditor {
        background-color: #0B0D12;
        color: #CBD5E1;
        border: 1px solid #262D3F;
        border-radius: 8px;
        padding: 8px;
        font-family: "Cascadia Code", "Consolas", monospace;
        font-size: 12px;
    }

    /* Scroll Area & Splitter */
    QScrollArea {
        border: none;
        background-color: transparent;
    }

    QScrollArea > QWidget > QWidget {
        background-color: transparent;
    }

    QSplitter::handle {
        background-color: #1C2230;
        width: 2px;
    }

    QScrollBar:vertical {
        background-color: transparent;
        width: 8px;
        margin: 2px;
    }

    QScrollBar::handle:vertical {
        background-color: #2A3347;
        border-radius: 4px;
        min-height: 28px;
    }

    QScrollBar::handle:vertical:hover {
        background-color: #3B4763;
    }

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
        height: 0px;
    }
    """
