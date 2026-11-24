"""Modern Dark Creator Studio QSS theme system for ATRO."""

from __future__ import annotations

from atro.ui.tokens import ThemeMode, ThemeTokens, get_theme_tokens

PALETTE = {
    "bg_base": "#090A0F",
    "bg_nav": "#12141C",
    "bg_surface": "#181B26",
    "bg_elevated": "#1F2332",
    "bg_hover": "#262B3D",
    "border": "#242836",
    "border_focus": "#7C3AED",
    "text_primary": "#F3F4F6",
    "text_secondary": "#D1D5DB",
    "text_muted": "#9CA3AF",
    "accent": "#7C3AED",
    "accent_hover": "#8B5CF6",
    "accent_pressed": "#6D28D9",
    "accent_secondary": "#06B6D4",
    "success": "#10B981",
    "warning": "#F59E0B",
    "error": "#EF4444",
}


def build_stylesheet(mode: ThemeMode = "dark") -> str:
    """Build cohesive Qt stylesheet for Dark Creator Studio / Light fallback."""
    t: ThemeTokens = get_theme_tokens(mode)
    return f"""
    * {{
        font-family: "Inter", "Plus Jakarta Sans", "Segoe UI", "Roboto", sans-serif;
        outline: none;
    }}

    QMainWindow, QDialog {{
        background-color: {t.bg_base};
        color: {t.text_primary};
    }}

    QWidget {{
        color: {t.text_primary};
        font-size: 13px;
    }}

    QToolTip {{
        background-color: {t.bg_elevated};
        color: {t.text_primary};
        border: 1px solid {t.border_hover};
        border-radius: 6px;
        padding: 5px 9px;
        font-size: 12px;
    }}

    QMenu {{
        background-color: {t.bg_surface};
        color: {t.text_primary};
        border: 1px solid {t.border_hover};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 7px 14px;
        border-radius: 6px;
        margin: 1px 0;
    }}
    QMenu::item:selected {{
        background-color: {t.accent};
        color: {t.accent_text};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t.border};
        margin: 5px 8px;
    }}

    /* ---------------- Sidebar Navigation Rail ---------------- */
    QFrame#Sidebar {{
        background-color: {t.bg_nav};
        border-right: 1px solid {t.border};
    }}
    QFrame#TopBar, QFrame#StudioTopBar {{
        background-color: {t.bg_nav};
        border-bottom: 1px solid {t.border};
    }}
    QLabel#BrandTitle {{
        font-size: 16px;
        font-weight: 800;
        color: {t.text_primary};
        letter-spacing: -0.3px;
    }}
    QLabel#VersionPill {{
        background-color: {t.bg_selected};
        border: 1px solid {t.border_hero};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: 10px;
        font-weight: 700;
        color: {t.accent_hover};
    }}
    QLabel#SidebarSectionLabel {{
        color: {t.text_muted};
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.9px;
        padding: 8px 10px 2px 10px;
    }}
    QPushButton#SidebarNavBtn {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 10px 12px;
        text-align: left;
        color: {t.text_muted};
        font-size: 13px;
        font-weight: 600;
    }}
    QPushButton#SidebarNavBtn:hover {{
        background-color: {t.bg_hover};
        color: {t.text_primary};
    }}
    QPushButton#SidebarNavBtn[active="true"], QPushButton#SidebarNavBtn:checked {{
        background-color: {t.bg_selected};
        border: 1px solid {t.border_hero};
        color: {t.text_primary};
        font-weight: 700;
    }}
    QPushButton#SidebarCollapseBtn {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 5px;
        color: {t.text_muted};
    }}
    QPushButton#SidebarCollapseBtn:hover {{
        background-color: {t.bg_hover};
        color: {t.text_primary};
        border-color: {t.border_hover};
    }}

    /* Legacy / Helper Badges */
    QPushButton#StepperNode {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        padding: 6px 12px;
        color: {t.text_muted};
        font-weight: 600;
    }}
    QPushButton#StepperNode[active="true"] {{
        background-color: {t.bg_selected};
        border: 1px solid {t.border_hero};
        color: {t.text_primary};
        font-weight: 700;
    }}
    QLabel#StepBadge {{
        background-color: {t.bg_selected};
        color: {t.accent_secondary};
        border: 1px solid {t.accent_secondary_border};
        border-radius: 8px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#StageNode {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: 4px 10px;
        font-size: 11px;
        font-weight: 600;
        color: {t.text_muted};
    }}
    QLabel#StageNode[state="active"] {{
        background-color: {t.bg_selected};
        border-color: {t.accent};
        color: {t.text_primary};
    }}
    QLabel#StageNode[state="done"] {{
        background-color: {t.success_bg};
        border-color: {t.success_border};
        color: {t.success};
    }}

    /* ---------------- Cards & Panels ---------------- */
    QFrame#Card, QFrame#WinUICard, QFrame#ProjectCard {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}
    QFrame#ProjectCard:hover {{
        border-color: {t.border_hover};
        background-color: {t.bg_elevated};
    }}
    QFrame#HeroCard, QFrame#HeroDropZone {{
        background-color: {t.bg_hero};
        border: 1px solid {t.border_hero};
        border-radius: 16px;
    }}
    QFrame#HeroDropZone[dragActive="true"] {{
        border: 2px dashed {t.accent_secondary};
        background-color: {t.accent_secondary_bg};
    }}
    QFrame#JobCard, QFrame#ClipCard {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}
    QFrame#JobCard:hover, QFrame#ClipCard:hover {{
        background-color: {t.bg_elevated};
        border-color: {t.border_hover};
    }}
    QFrame#JobCard[selected="true"], QFrame#ClipCard[selected="true"] {{
        background-color: {t.bg_selected};
        border: 1px solid {t.accent};
    }}
    QFrame#StyleCard, QFrame#VisualChoiceCard {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}
    QFrame#StyleCard:hover, QFrame#VisualChoiceCard:hover {{
        background-color: {t.bg_elevated};
        border-color: {t.border_hover};
    }}
    QFrame#StyleCard[selected="true"], QFrame#VisualChoiceCard[selected="true"] {{
        background-color: {t.bg_selected};
        border: 2px solid {t.accent};
    }}

    QFrame#QuickToggleRow {{
        background-color: {t.bg_nav};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}
    QFrame#StudioStage {{
        background-color: {t.bg_base};
    }}
    QFrame#StudioPlayerBar {{
        background-color: {t.bg_nav};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}
    QFrame#TranscriptPanel, QFrame#InspectorPanel {{
        background-color: {t.bg_nav};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}
    QFrame#TimelinePanel {{
        background-color: {t.bg_input};
        border-top: 1px solid {t.border};
    }}

    QFrame#FloatingBar {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border_hero};
        border-radius: 12px;
    }}
    QFrame#ToastBanner {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border_hover};
        border-radius: 12px;
    }}
    QFrame#ToastBanner[tone="ok"] {{ border-color: {t.success_border}; background-color: {t.success_bg}; }}
    QFrame#ToastBanner[tone="warn"] {{ border-color: {t.warning_border}; background-color: {t.warning_bg}; }}
    QFrame#ToastBanner[tone="err"] {{ border-color: {t.error_border}; background-color: {t.error_bg}; }}
    QFrame#ToastBanner[tone="info"] {{ border-color: {t.info_border}; background-color: {t.info_bg}; }}

    QFrame#AlertBanner {{
        background-color: {t.warning_bg};
        border: 1px solid {t.warning_border};
        border-radius: 10px;
        padding: 8px 12px;
    }}
    QFrame#AlertBanner[tone="err"] {{
        background-color: {t.error_bg};
        border: 1px solid {t.error_border};
    }}
    QFrame#AlertBanner[tone="ok"] {{
        background-color: {t.success_bg};
        border: 1px solid {t.success_border};
    }}

    /* ---------------- Typography ---------------- */
    QLabel#HeroTitle {{
        font-size: 24px;
        font-weight: 800;
        color: {t.text_primary};
        letter-spacing: -0.4px;
    }}
    QLabel#SectionTitle {{
        font-size: 14px;
        font-weight: 700;
        color: {t.text_primary};
    }}
    QLabel#FieldLabel {{
        font-size: 12px;
        font-weight: 600;
        color: {t.text_secondary};
    }}
    QLabel#MutedText {{
        font-size: 12px;
        color: {t.text_muted};
    }}
    QLabel#StatusPill {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 700;
        color: {t.text_secondary};
    }}
    QLabel#StatusPill[tone="ok"] {{
        background-color: {t.success_bg};
        border-color: {t.success_border};
        color: {t.success};
    }}
    QLabel#StatusPill[tone="warn"] {{
        background-color: {t.warning_bg};
        border-color: {t.warning_border};
        color: {t.warning};
    }}
    QLabel#StatusPill[tone="err"] {{
        background-color: {t.error_bg};
        border-color: {t.error_border};
        color: {t.error};
    }}
    QLabel#StatusPill[tone="cyan"] {{
        background-color: {t.accent_secondary_bg};
        border-color: {t.accent_secondary_border};
        color: {t.accent_secondary};
    }}
    QLabel#ViralityBadge {{
        background-color: {t.success_bg};
        border: 1px solid {t.success_border};
        border-radius: 10px;
        padding: 4px 10px;
        font-size: 12px;
        font-weight: 800;
        color: {t.success};
    }}
    QLabel#ViralityBadge[tier="mid"] {{
        background-color: {t.warning_bg};
        border-color: {t.warning_border};
        color: {t.warning};
    }}
    QLabel#ViralityBadge[tier="low"] {{
        background-color: {t.bg_elevated};
        border-color: {t.border_hover};
        color: {t.text_secondary};
    }}

    /* ---------------- Inputs ---------------- */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {{
        background-color: {t.bg_input};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 7px 11px;
        color: {t.text_primary};
        selection-background-color: {t.accent};
        min-height: 18px;
    }}
    QLineEdit#HeroInput {{
        background-color: {t.bg_base};
        border: 1px solid {t.border_hover};
        border-radius: 12px;
        padding: 11px 16px;
        font-size: 14px;
        min-height: 24px;
    }}
    QLineEdit#HeroInput:focus {{
        border: 1.5px solid {t.accent};
    }}
    QLineEdit#TitleEditable {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 4px 8px;
        font-size: 14px;
        font-weight: 700;
        color: {t.text_primary};
    }}
    QLineEdit#TitleEditable:hover {{
        background-color: {t.bg_surface};
        border-color: {t.border};
    }}
    QLineEdit#TitleEditable:focus {{
        background-color: {t.bg_input};
        border-color: {t.accent};
    }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QTextEdit:hover {{
        border-color: {t.border_hover};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {{
        border: 1.5px solid {t.accent};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border_hover};
        border-radius: 8px;
        padding: 4px;
        selection-background-color: {t.accent};
        selection-color: {t.accent_text};
    }}

    /* ---------------- Buttons & Chips ---------------- */
    QPushButton {{
        background-color: {t.bg_elevated};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 7px 14px;
        color: {t.text_primary};
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {t.bg_hover};
        border-color: {t.border_hover};
    }}
    QPushButton:pressed {{
        background-color: {t.bg_surface};
    }}
    QPushButton:disabled {{
        color: {t.text_muted};
        background-color: {t.bg_nav};
        border-color: {t.border};
    }}

    QPushButton#PrimaryButton, QPushButton#HeroButton {{
        background-color: {t.accent};
        border: 1px solid {t.accent};
        color: {t.accent_text};
        font-weight: 700;
    }}
    QPushButton#HeroButton {{
        border-radius: 12px;
        padding: 11px 22px;
        font-size: 14px;
    }}
    QPushButton#PrimaryButton:hover, QPushButton#HeroButton:hover {{
        background-color: {t.accent_hover};
        border-color: {t.accent_hover};
    }}
    QPushButton#PrimaryButton:pressed, QPushButton#HeroButton:pressed {{
        background-color: {t.accent_pressed};
        border-color: {t.accent_pressed};
    }}

    QPushButton#CyanButton {{
        background-color: {t.accent_secondary_bg};
        border: 1px solid {t.accent_secondary_border};
        color: {t.accent_secondary};
        font-weight: 700;
    }}
    QPushButton#CyanButton:hover, QPushButton#CyanButton:checked {{
        background-color: {t.accent_secondary};
        color: #061319;
    }}

    QPushButton#SmallAction {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 5px 11px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton#SmallAction:hover {{
        background-color: {t.bg_hover};
        border-color: {t.border_hover};
    }}
    QPushButton#SmallAction:checked {{
        background-color: {t.bg_selected};
        border-color: {t.accent};
        color: {t.text_primary};
    }}

    QPushButton#GhostButton {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 6px 10px;
        color: {t.text_secondary};
    }}
    QPushButton#GhostButton:hover {{
        background-color: {t.bg_hover};
        color: {t.text_primary};
    }}

    QPushButton#ModeSwitch {{
        background-color: transparent;
        border: none;
        border-radius: 8px;
        padding: 6px 12px;
        color: {t.text_muted};
        font-weight: 700;
        font-size: 12px;
    }}
    QPushButton#ModeSwitch:checked {{
        background-color: {t.accent};
        color: {t.accent_text};
    }}

    QPushButton#DurationChip {{
        background-color: {t.bg_surface};
        border: 1px solid {t.border};
        border-radius: 14px;
        padding: 5px 14px;
        color: {t.text_secondary};
        font-size: 12px;
        font-weight: 700;
    }}
    QPushButton#DurationChip:hover {{
        border-color: {t.border_hover};
        background-color: {t.bg_hover};
    }}
    QPushButton#DurationChip:checked {{
        background-color: {t.accent};
        border-color: {t.accent};
        color: {t.accent_text};
    }}

    QPushButton#DangerButton {{
        background-color: {t.error_bg};
        border: 1px solid {t.error_border};
        color: {t.error};
        font-weight: 700;
    }}
    QPushButton#DangerButton:hover {{
        background-color: {t.error};
        color: #FFFFFF;
    }}

    /* Interactive Transcript Word Chips */
    QPushButton#WordChip {{
        background-color: {t.bg_surface};
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 3px 6px;
        color: {t.text_secondary};
        font-size: 12px;
        font-weight: 500;
    }}
    QPushButton#WordChip:hover {{
        background-color: {t.bg_hover};
        border-color: {t.border_hover};
        color: {t.text_primary};
    }}
    QPushButton#WordChip[active="true"] {{
        background-color: {t.accent_secondary_bg};
        border: 1px solid {t.accent_secondary};
        color: {t.accent_secondary};
        font-weight: 700;
    }}
    QPushButton#WordChip[edited="true"] {{
        border-bottom: 2px solid {t.accent};
    }}

    /* ---------------- CheckBox / Progress / Tabs ---------------- */
    QCheckBox {{
        spacing: 8px;
        color: {t.text_secondary};
        font-weight: 500;
    }}
    QCheckBox::indicator {{
        width: 17px;
        height: 17px;
        border-radius: 5px;
        border: 1.5px solid {t.border_hover};
        background-color: {t.bg_input};
    }}
    QCheckBox::indicator:hover {{ border-color: {t.accent}; }}
    QCheckBox::indicator:checked {{
        border-color: {t.accent};
        background-color: {t.accent};
    }}

    QProgressBar {{
        background-color: {t.bg_input};
        border: 1px solid {t.border};
        border-radius: 6px;
        text-align: center;
        color: {t.text_primary};
        font-weight: 700;
        font-size: 11px;
        min-height: 12px;
        max-height: 16px;
    }}
    QProgressBar::chunk {{
        background-color: {t.accent};
        border-radius: 5px;
    }}

    QTabWidget::pane {{
        border: none;
        border-top: 1px solid {t.border};
        background-color: transparent;
        padding-top: 8px;
    }}
    QTabBar {{ background-color: transparent; }}
    QTabBar::tab {{
        background-color: transparent;
        color: {t.text_muted};
        padding: 8px 12px;
        margin-right: 2px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 600;
        font-size: 12px;
    }}
    QTabBar::tab:hover {{
        color: {t.text_primary};
        background-color: {t.bg_hover};
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{
        color: {t.text_primary};
        border-bottom: 2px solid {t.accent};
        font-weight: 700;
    }}

    /* ---------------- Log, Scroll, Splitter, Tables, Sliders ---------------- */
    QPlainTextEdit#LogEditor {{
        background-color: {t.bg_input};
        color: {t.text_secondary};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: 10px;
        font-family: "Cascadia Code", "Consolas", monospace;
        font-size: 12px;
    }}
    QScrollArea {{
        border: none;
        background-color: transparent;
    }}
    QScrollArea > QWidget > QWidget {{
        background-color: transparent;
    }}
    QSplitter::handle {{
        background-color: {t.border};
        width: 2px;
        height: 2px;
    }}
    QSplitter::handle:hover {{
        background-color: {t.accent};
    }}
    QScrollBar:vertical {{
        background-color: transparent;
        width: 6px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {t.border_hover};
        border-radius: 3px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background-color: {t.accent}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background-color: transparent;
        height: 6px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background-color: {t.border_hover};
        border-radius: 3px;
        min-width: 28px;
    }}
    QScrollBar::handle:horizontal:hover {{ background-color: {t.accent}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
        width: 0px;
    }}

    QGroupBox {{
        border: 1px solid {t.border};
        border-radius: 10px;
        margin-top: 12px;
        padding: 12px 10px 10px 10px;
        font-weight: 700;
        font-size: 12px;
        color: {t.text_primary};
        background-color: {t.bg_surface};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 6px;
        color: {t.accent_secondary};
    }}

    QListWidget, QTableWidget {{
        background-color: {t.bg_input};
        border: 1px solid {t.border};
        border-radius: 8px;
        gridline-color: {t.border};
        color: {t.text_primary};
    }}
    QListWidget::item {{
        padding: 6px 8px;
        border-radius: 6px;
    }}
    QListWidget::item:selected {{
        background-color: {t.bg_selected};
        color: {t.text_primary};
    }}
    QHeaderView::section {{
        background-color: {t.bg_elevated};
        color: {t.text_secondary};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 6px 8px;
        font-weight: 700;
        font-size: 11px;
    }}

    QSlider::groove:horizontal {{
        height: 5px;
        background: {t.bg_elevated};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {t.accent};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {t.text_primary};
        border: 2px solid {t.accent};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {t.accent_hover};
        border-color: {t.text_primary};
    }}
    """
