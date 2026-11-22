"""WinUI 3 Fluent Dark + Light QSS stylesheet powered by ATRO V2 Design Tokens."""

from __future__ import annotations

from atro.ui.tokens import ThemeMode, ThemeTokens, get_theme_tokens

PALETTE = {
    "bg_base": "#0F0E0D",
    "bg_nav": "#171514",
    "bg_surface": "#1E1B19",
    "bg_elevated": "#26221F",
    "bg_hover": "#312B27",
    "border": "#322C28",
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


def build_stylesheet(mode: ThemeMode = "dark") -> str:
    """Return clean Fluent QSS stylesheet for dark (default) or light theme."""
    t: ThemeTokens = get_theme_tokens(mode)
    return f"""
    QWidget {{
        background-color: {t.bg_base};
        color: {t.text_primary};
        font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
        font-size: 13px;
    }}

    QMainWindow, QDialog {{
        background-color: {t.bg_base};
    }}

    /* WinUI 3 Surface Hierarchy */
    QFrame#TopBar {{
        background-color: {t.bg_nav};
        border: 1px solid {t.border};
        border-bottom: 2px solid {t.accent};
        border-radius: 12px;
    }}

    QFrame#Card, QFrame#WinUICard, QFrame#JobCard, QFrame#ClipCard {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}

    QFrame#FloatingBar {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border_hero};
        border-radius: 12px;
    }}

    QFrame#ToastBanner {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border};
        border-left: 4px solid {t.accent};
        border-radius: 10px;
    }}
    QFrame#ToastBanner[tone="ok"] {{
        border-left: 4px solid {t.success};
    }}
    QFrame#ToastBanner[tone="warn"] {{
        border-left: 4px solid {t.warning};
    }}
    QFrame#ToastBanner[tone="error"] {{
        border-left: 4px solid {t.error};
    }}
    QFrame#ToastBanner[tone="info"] {{
        border-left: 4px solid {t.info};
    }}
    QFrame#ToastBanner QLabel {{
        background: transparent;
    }}

    QFrame#HeroCard {{
        background-color: {t.bg_hero};
        border: 1px solid {t.border_hero};
        border-left: 4px solid {t.accent};
        border-radius: 12px;
    }}

    QFrame#StyleCard {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}

    QFrame#StyleCard:hover {{
        background-color: {t.bg_hover};
        border: 1px solid {t.border_hero};
    }}

    QFrame#StyleCard[selected="true"] {{
        background-color: {t.bg_selected};
        border: 2px solid {t.accent};
    }}

    QFrame#JobCard[selected="true"] {{
        background-color: {t.bg_selected};
        border: 2px solid {t.accent};
    }}

    QFrame#JobCard:hover {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border_hover};
    }}

    QFrame#JobCard[selected="true"]:hover {{
        border: 2px solid {t.accent_hover};
    }}

    QFrame#AlertBanner[severity="error"] {{
        background-color: {t.error_bg};
        border: 1px solid {t.error_border};
        border-radius: 10px;
    }}

    QFrame#AlertBanner[severity="warning"] {{
        background-color: {t.warning_bg};
        border: 1px solid {t.warning_border};
        border-radius: 10px;
    }}

    QFrame#AlertBanner QLabel {{
        background: transparent;
    }}

    /* Typography */
    QLabel {{
        background-color: transparent;
    }}

    QLabel#BrandTitle {{
        font-size: 18px;
        font-weight: 800;
        letter-spacing: 1px;
        color: {t.accent_text};
        background-color: {t.accent};
        border-radius: 8px;
        padding: 4px 12px;
    }}

    QLabel#HeroTitle {{
        font-size: 18px;
        font-weight: 700;
        color: {t.text_primary};
    }}

    QLabel#SectionTitle {{
        font-size: 14px;
        font-weight: 700;
        color: {t.text_primary};
    }}

    QLabel#StepBadge {{
        background-color: {t.bg_selected};
        color: {t.accent};
        border: 1px solid {t.border_hero};
        border-radius: 8px;
        padding: 3px 10px;
        font-size: 12px;
        font-weight: 700;
    }}

    QLabel#MutedText {{
        color: {t.text_muted};
        font-size: 12px;
    }}

    QLabel#FieldLabel {{
        color: {t.text_secondary};
        font-size: 12px;
        font-weight: 600;
    }}

    QLabel#VersionPill {{
        background-color: {t.bg_elevated};
        color: {t.accent};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 3px 8px;
        font-size: 11px;
        font-weight: 600;
    }}

    /* WinUI 3 Stepper Bar Nodes */
    QPushButton#StepperNode {{
        background-color: {t.bg_elevated};
        color: {t.text_muted};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 6px 11px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton#StepperNode:hover {{
        background-color: {t.bg_hover};
        color: {t.text_primary};
        border: 1px solid {t.border_hover};
    }}

    QPushButton#StepperNode[active="true"] {{
        background-color: {t.accent};
        color: {t.accent_text};
        border: 1px solid {t.accent};
        font-weight: 700;
    }}

    QPushButton#StepperNode[done="true"] {{
        background-color: {t.bg_selected};
        color: {t.accent};
        border: 1px solid {t.border_hero};
        font-weight: 600;
    }}

    /* Stage Progress Indicators */
    QLabel#StageNode {{
        border-radius: 7px;
        padding: 5px 10px;
        font-size: 12px;
        font-weight: 600;
        background-color: {t.bg_elevated};
        color: {t.text_muted};
        border: 1px solid {t.border};
    }}

    QLabel#StageNode[state="active"] {{
        background-color: {t.bg_selected};
        color: {t.accent};
        border: 1px solid {t.accent};
    }}

    QLabel#StageNode[state="done"] {{
        background-color: {t.success_bg};
        color: {t.success};
        border: 1px solid {t.success_border};
    }}

    /* Status & Preflight Pills */
    QLabel#StatusPill {{
        border-radius: 7px;
        padding: 4px 10px;
        font-size: 11px;
        font-weight: 700;
    }}

    QLabel#StatusPill[tone="ok"] {{
        background-color: {t.success_bg};
        color: {t.success};
        border: 1px solid {t.success_border};
    }}

    QLabel#StatusPill[tone="warn"] {{
        background-color: {t.warning_bg};
        color: {t.warning};
        border: 1px solid {t.warning_border};
    }}

    QLabel#StatusPill[tone="error"] {{
        background-color: {t.error_bg};
        color: {t.error};
        border: 1px solid {t.error_border};
    }}

    QLabel#StatusPill[tone="info"] {{
        background-color: {t.bg_selected};
        color: {t.accent};
        border: 1px solid {t.border_hero};
    }}

    QLabel#StatusPill[tone="neutral"] {{
        background-color: {t.bg_elevated};
        color: {t.text_secondary};
        border: 1px solid {t.border};
    }}

    /* Inputs */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
        background-color: {t.bg_input};
        color: {t.text_primary};
        border: 1px solid {t.border};
        border-radius: 7px;
        padding: 6px 10px;
        min-height: 22px;
        selection-background-color: {t.accent};
    }}

    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border_hover};
    }}

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border: 1px solid {t.border_hover};
        border-bottom: 2px solid {t.accent};
    }}

    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {t.bg_surface};
        color: {t.text_primary};
        border: 1px solid {t.border};
        selection-background-color: {t.accent};
        selection-color: {t.accent_text};
        outline: 0;
    }}

    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 18px;
        border: none;
        background: transparent;
    }}

    /* Buttons */
    QPushButton {{
        background-color: {t.bg_elevated};
        color: {t.text_primary};
        border: 1px solid {t.border};
        border-radius: 7px;
        padding: 7px 14px;
        font-weight: 600;
    }}

    QPushButton:hover {{
        background-color: {t.bg_hover};
        border: 1px solid {t.border_hover};
    }}

    QPushButton:pressed {{
        background-color: {t.bg_surface};
    }}

    QPushButton:disabled {{
        background-color: {t.bg_base};
        color: {t.text_muted};
        border: 1px solid {t.border};
    }}

    QPushButton#PrimaryButton, QPushButton#HeroButton {{
        background-color: {t.accent};
        color: {t.accent_text};
        border: 1px solid {t.accent};
        font-weight: 700;
        font-size: 13px;
        padding: 9px 18px;
        border-radius: 8px;
    }}

    QPushButton#HeroButton {{
        font-size: 14px;
        padding: 10px 22px;
    }}

    QPushButton#PrimaryButton:hover, QPushButton#HeroButton:hover {{
        background-color: {t.accent_hover};
    }}

    QPushButton#PrimaryButton:pressed, QPushButton#HeroButton:pressed {{
        background-color: {t.accent_pressed};
    }}

    QPushButton#DangerButton {{
        background-color: {t.error_bg};
        color: {t.error};
        border: 1px solid {t.error_border};
    }}

    QPushButton#SmallAction {{
        padding: 5px 10px;
        font-size: 12px;
        border-radius: 7px;
    }}

    QPushButton#ModeSwitch {{
        background-color: {t.bg_elevated};
        color: {t.text_secondary};
        border: 1px solid {t.border};
        border-radius: 7px;
        padding: 5px 12px;
        font-size: 12px;
        font-weight: 600;
    }}

    QPushButton#ModeSwitch:hover {{
        color: {t.text_primary};
        background-color: {t.bg_hover};
    }}

    QPushButton#ModeSwitch:checked {{
        background-color: {t.accent};
        color: {t.accent_text};
        border: 1px solid {t.accent};
        font-weight: 700;
    }}

    /* Progress Bar */
    QProgressBar {{
        background-color: {t.bg_elevated};
        border: none;
        border-radius: 4px;
        min-height: 7px;
        max-height: 7px;
        text-align: center;
    }}

    QProgressBar::chunk {{
        background-color: {t.accent};
        border-radius: 4px;
    }}
    QProgressBar[tone="ok"]::chunk {{ background-color: {t.success}; }}
    QProgressBar[tone="error"]::chunk {{ background-color: {t.error}; }}
    QProgressBar[tone="warn"]::chunk {{ background-color: {t.warning}; }}

    /* Checkboxes */
    QCheckBox {{
        spacing: 8px;
        color: {t.text_primary};
        font-size: 12px;
        background-color: transparent;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {t.border_hover};
        background-color: {t.bg_input};
    }}
    QCheckBox::indicator:hover {{ border: 1px solid {t.accent}; }}
    QCheckBox::indicator:checked {{
        background-color: {t.accent};
        border: 1px solid {t.accent};
    }}

    /* Tabs */
    QTabWidget::pane {{
        border: none;
        border-top: 2px solid {t.border};
        background-color: transparent;
        padding-top: 8px;
    }}
    QTabBar {{ background-color: transparent; }}
    QTabBar::tab {{
        background-color: transparent;
        color: {t.text_muted};
        padding: 8px 13px;
        margin-right: 4px;
        border: none;
        border-bottom: 3px solid transparent;
        font-weight: 600;
    }}
    QTabBar::tab:hover {{
        color: {t.text_primary};
        background-color: {t.bg_hover};
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{
        color: {t.text_primary};
        border-bottom: 3px solid {t.accent};
        font-weight: 700;
    }}

    /* Log Viewer */
    QPlainTextEdit#LogEditor {{
        background-color: {t.bg_input};
        color: {t.text_secondary};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 8px;
        font-family: "Cascadia Code", "Consolas", monospace;
        font-size: 12px;
    }}

    /* Scroll Area & Splitter */
    QScrollArea {{
        border: none;
        background-color: transparent;
    }}
    QScrollArea > QWidget > QWidget {{
        background-color: transparent;
    }}
    QSplitter::handle {{
        background-color: {t.border};
        width: 3px;
    }}
    QSplitter::handle:hover {{
        background-color: {t.accent};
    }}
    QScrollBar:vertical {{
        background-color: transparent;
        width: 8px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {t.border_hover};
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background-color: {t.accent}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
        height: 0px;
    }}

    /* Studio GroupBoxes, Lists & Sliders */
    QGroupBox {{
        border: 1px solid {t.border};
        border-radius: 10px;
        margin-top: 12px;
        padding: 12px 12px 10px 12px;
        font-weight: 700;
        color: {t.text_primary};
        background-color: {t.bg_surface};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 8px;
        color: {t.accent};
    }}

    QListWidget, QTableWidget {{
        background-color: {t.bg_input};
        border: 1px solid {t.border};
        border-radius: 8px;
        gridline-color: {t.border};
        color: {t.text_primary};
    }}
    QHeaderView::section {{
        background-color: {t.bg_elevated};
        color: {t.accent};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 6px 8px;
        font-weight: 700;
    }}

    QSlider::groove:horizontal {{
        height: 6px;
        background: {t.bg_elevated};
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{
        background: {t.accent};
        border-radius: 3px;
    }}
    QSlider::handle:horizontal {{
        background: {t.accent};
        border: 2px solid {t.accent_text};
        width: 15px;
        height: 15px;
        margin: -5px 0;
        border-radius: 8px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {t.accent_hover};
    }}
    """
