"""WinUI 3 Fluent Dark + Orange Base QSS stylesheet and design tokens for ATRO."""

from __future__ import annotations

PALETTE = {
    "bg_base": "#11100F",
    "bg_nav": "#181614",
    "bg_surface": "#1F1C1A",
    "bg_elevated": "#272320",
    "bg_hover": "#312B27",
    "border": "#332D29",
    "border_focus": "#F97316",
    "text_primary": "#FFFBEB",
    "text_secondary": "#E7E5E4",
    "text_muted": "#A8A29E",
    "accent": "#F97316",
    "accent_hover": "#EA580C",
    "accent_pressed": "#C2410C",
    "success": "#10B981",
    "warning": "#F59E0B",
    "error": "#EF4444",
}


def build_stylesheet() -> str:
    """Return modern WinUI 3 Fluent Dark + Orange QSS stylesheet."""
    return """
    QWidget {
        background-color: #11100F;
        color: #FFFBEB;
        font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
        font-size: 13px;
    }

    QMainWindow, QDialog {
        background-color: #11100F;
    }

    /* WinUI 3 Surface Hierarchy */
    QFrame#TopBar {
        background-color: #181614;
        border: 1px solid #332D29;
        border-bottom: 2px solid #F97316;
        border-radius: 12px;
    }

    QFrame#Card, QFrame#WinUICard, QFrame#JobCard, QFrame#ClipCard {
        background-color: #1F1C1A;
        border: 1px solid #332D29;
        border-radius: 10px;
    }

    QFrame#HeroCard {
        background-color: #1E1713;
        border: 1px solid #59331B;
        border-left: 4px solid #F97316;
        border-radius: 12px;
    }

    QFrame#StyleCard {
        background-color: #221E1B;
        border: 1px solid #3A322D;
        border-radius: 10px;
    }

    QFrame#StyleCard:hover {
        background-color: #2B2521;
        border: 1px solid #7C4A2A;
    }

    QFrame#StyleCard[selected="true"] {
        background-color: #2B1D14;
        border: 2px solid #F97316;
    }

    QFrame#JobCard[selected="true"] {
        background-color: #2B1D14;
        border: 2px solid #F97316;
    }

    QFrame#JobCard:hover {
        background-color: #272320;
        border: 1px solid #574A40;
    }

    QFrame#JobCard[selected="true"]:hover {
        border: 2px solid #FB923C;
    }

    QFrame#AlertBanner[severity="error"] {
        background-color: #2A1616;
        border: 1px solid #7F1D1D;
        border-radius: 10px;
    }

    QFrame#AlertBanner[severity="warning"] {
        background-color: #2B1F11;
        border: 1px solid #9A3412;
        border-radius: 10px;
    }

    QFrame#AlertBanner QLabel {
        background: transparent;
    }

    /* Typography */
    QLabel {
        background-color: transparent;
    }

    QLabel#BrandTitle {
        font-size: 19px;
        font-weight: 800;
        letter-spacing: 1px;
        color: #FFFFFF;
        background-color: #F97316;
        border-radius: 8px;
        padding: 4px 12px;
    }

    QLabel#HeroTitle {
        font-size: 18px;
        font-weight: 700;
        color: #FFFBEB;
    }

    QLabel#SectionTitle {
        font-size: 14px;
        font-weight: 700;
        color: #FFFBEB;
    }

    QLabel#StepBadge {
        background-color: #2E1D12;
        color: #FB923C;
        border: 1px solid #9A3412;
        border-radius: 8px;
        padding: 3px 10px;
        font-size: 12px;
        font-weight: 700;
    }

    QLabel#MutedText {
        color: #A8A29E;
        font-size: 12px;
    }

    QLabel#FieldLabel {
        color: #E7E5E4;
        font-size: 12px;
        font-weight: 600;
    }

    QLabel#VersionPill {
        background-color: #272320;
        color: #FDBA74;
        border: 1px solid #443A33;
        border-radius: 6px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 600;
    }

    /* WinUI 3 Stepper Bar Nodes */
    QPushButton#StepperNode {
        background-color: #221E1B;
        color: #A8A29E;
        border: 1px solid #38302B;
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 12px;
        font-weight: 600;
    }

    QPushButton#StepperNode:hover {
        background-color: #2D2723;
        color: #FFFBEB;
        border: 1px solid #59463A;
    }

    QPushButton#StepperNode[active="true"] {
        background-color: #F97316;
        color: #FFFFFF;
        border: 1px solid #FB923C;
        font-weight: 700;
    }

    QPushButton#StepperNode[done="true"] {
        background-color: #2A1E17;
        color: #FB923C;
        border: 1px solid #9A3412;
        font-weight: 600;
    }

    /* Stage Progress Indicators */
    QLabel#StageNode {
        border-radius: 7px;
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 600;
        background-color: #25211E;
        color: #78716C;
        border: 1px solid #332D29;
    }

    QLabel#StageNode[state="active"] {
        background-color: #362113;
        color: #FB923C;
        border: 1px solid #F97316;
    }

    QLabel#StageNode[state="done"] {
        background-color: #122921;
        color: #34D399;
        border: 1px solid #155E4B;
    }

    /* Status & Preflight Pills */
    QLabel#StatusPill {
        border-radius: 7px;
        padding: 4px 10px;
        font-size: 11px;
        font-weight: 700;
    }

    QLabel#StatusPill[tone="ok"] {
        background-color: #102A22;
        color: #34D399;
        border: 1px solid #155E4B;
    }

    QLabel#StatusPill[tone="warn"] {
        background-color: #2E1F10;
        color: #FBBF24;
        border: 1px solid #78350F;
    }

    QLabel#StatusPill[tone="error"] {
        background-color: #2B1519;
        color: #F87171;
        border: 1px solid #7F1D1D;
    }

    QLabel#StatusPill[tone="info"] {
        background-color: #2E1D12;
        color: #FB923C;
        border: 1px solid #9A3412;
    }

    QLabel#StatusPill[tone="neutral"] {
        background-color: #272320;
        color: #D6D3D1;
        border: 1px solid #3D3530;
    }

    /* WinUI 3 Fluent Inputs (elevated surface + orange bottom accent on focus) */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background-color: #25211E;
        color: #FFFBEB;
        border: 1px solid #3A322D;
        border-radius: 7px;
        padding: 7px 10px;
        min-height: 22px;
        selection-background-color: #F97316;
    }

    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
        background-color: #2C2723;
        border: 1px solid #52453E;
    }

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
        background-color: #1B1816;
        border: 1px solid #52453E;
        border-bottom: 2px solid #F97316;
    }

    QLineEdit::placeholder {
        color: #78716C;
    }

    QComboBox::drop-down {
        border: none;
        width: 24px;
    }

    QComboBox QAbstractItemView {
        background-color: #1F1C1A;
        color: #FFFBEB;
        border: 1px solid #3A322D;
        selection-background-color: #9A3412;
        outline: 0;
    }

    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
        width: 18px;
        border: none;
        background: transparent;
    }

    /* WinUI Buttons */
    QPushButton {
        background-color: #272320;
        color: #FFFBEB;
        border: 1px solid #3F3630;
        border-radius: 7px;
        padding: 7px 14px;
        font-weight: 600;
    }

    QPushButton:hover {
        background-color: #312B27;
        border: 1px solid #5C4D43;
    }

    QPushButton:pressed {
        background-color: #1D1A18;
    }

    QPushButton:disabled {
        background-color: #1A1715;
        color: #78716C;
        border: 1px solid #292420;
    }

    QPushButton#PrimaryButton, QPushButton#HeroButton {
        background-color: #F97316;
        color: #FFFFFF;
        border: 1px solid #FB923C;
        font-weight: 700;
        font-size: 13px;
        padding: 9px 18px;
        border-radius: 8px;
    }

    QPushButton#HeroButton {
        font-size: 14px;
        padding: 11px 22px;
    }

    QPushButton#PrimaryButton:hover, QPushButton#HeroButton:hover {
        background-color: #EA580C;
        border: 1px solid #F97316;
    }

    QPushButton#PrimaryButton:pressed, QPushButton#HeroButton:pressed {
        background-color: #C2410C;
        border: 1px solid #EA580C;
    }

    QPushButton#PrimaryButton:disabled, QPushButton#HeroButton:disabled {
        background-color: #4A2B16;
        color: #A8A29E;
        border: 1px solid #5E351B;
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
        padding: 5px 11px;
        font-size: 12px;
        border-radius: 7px;
    }

    QPushButton#ModeSwitch {
        background-color: #1B1816;
        color: #D6D3D1;
        border: 1px solid #3A322D;
        border-radius: 7px;
        padding: 6px 14px;
        font-size: 12px;
        font-weight: 600;
    }

    QPushButton#ModeSwitch:hover {
        color: #FFFBEB;
        border: 1px solid #9A3412;
        background-color: #26201C;
    }

    QPushButton#ModeSwitch:checked {
        background-color: #F97316;
        color: #FFFFFF;
        border: 1px solid #FB923C;
        font-weight: 700;
    }

    /* Progress Bar */
    QProgressBar {
        background-color: #161412;
        border: none;
        border-radius: 4px;
        min-height: 7px;
        max-height: 7px;
        text-align: center;
    }

    QProgressBar::chunk {
        background-color: #F97316;
        border-radius: 4px;
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

    /* Checkboxes */
    QCheckBox {
        spacing: 8px;
        color: #F5F5F4;
        font-size: 12px;
        background-color: transparent;
    }

    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid #574A40;
        background-color: #1B1816;
    }

    QCheckBox::indicator:hover {
        border: 1px solid #F97316;
    }

    QCheckBox::indicator:checked {
        background-color: #F97316;
        border: 1px solid #FB923C;
    }

    /* Tabs (WinUI Pivot style) */
    QTabWidget::pane {
        border: none;
        border-top: 2px solid #332D29;
        background-color: transparent;
        padding-top: 10px;
    }

    QTabBar {
        background-color: transparent;
    }

    QTabBar::tab {
        background-color: transparent;
        color: #A8A29E;
        padding: 8px 14px;
        margin-right: 4px;
        border: none;
        border-bottom: 3px solid transparent;
        font-weight: 600;
    }

    QTabBar::tab:hover {
        color: #FFFBEB;
        background-color: #221E1B;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }

    QTabBar::tab:selected {
        color: #FFFFFF;
        border-bottom: 3px solid #F97316;
        font-weight: 700;
    }

    /* Log Viewer */
    QPlainTextEdit#LogEditor {
        background-color: #0D0B0A;
        color: #E7E5E4;
        border: 1px solid #332D29;
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
        background-color: #272320;
        width: 3px;
    }

    QSplitter::handle:hover {
        background-color: #F97316;
    }

    QScrollBar:vertical {
        background-color: transparent;
        width: 8px;
        margin: 2px;
    }

    QScrollBar::handle:vertical {
        background-color: #4A3E36;
        border-radius: 4px;
        min-height: 28px;
    }

    QScrollBar::handle:vertical:hover {
        background-color: #F97316;
    }

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
        height: 0px;
    }

    /* Studio GroupBoxes, Sliders & Tables */
    QGroupBox {
        border: 1px solid #332D29;
        border-radius: 10px;
        margin-top: 14px;
        padding: 14px 12px 12px 12px;
        font-weight: 700;
        color: #FFFBEB;
        background-color: #191614;
    }

    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 8px;
        color: #FB923C;
    }

    QListWidget, QTableWidget {
        background-color: #141210;
        border: 1px solid #332D29;
        border-radius: 8px;
        gridline-color: #292420;
        color: #FFFBEB;
    }

    QHeaderView::section {
        background-color: #221E1B;
        color: #FDBA74;
        border: none;
        border-bottom: 1px solid #332D29;
        padding: 6px 8px;
        font-weight: 700;
    }

    QSlider::groove:horizontal {
        height: 7px;
        background: #2B2521;
        border-radius: 4px;
    }

    QSlider::sub-page:horizontal {
        background: #F97316;
        border-radius: 4px;
    }

    QSlider::handle:horizontal {
        background: #F97316;
        border: 2px solid #FFFBEB;
        width: 16px;
        height: 16px;
        margin: -5px 0;
        border-radius: 9px;
    }

    QSlider::handle:horizontal:hover {
        background: #FB923C;
    }
    """
