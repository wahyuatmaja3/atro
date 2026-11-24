"""Design tokens for ATRO Dark Creator Studio UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ThemeMode = Literal["dark", "light"]

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 12


@dataclass(frozen=True)
class ThemeTokens:
    mode: ThemeMode
    bg_base: str
    bg_nav: str
    bg_surface: str
    bg_elevated: str
    bg_hover: str
    bg_input: str
    bg_hero: str
    bg_selected: str
    border: str
    border_hover: str
    border_hero: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    accent_secondary: str
    accent_secondary_bg: str
    accent_secondary_border: str
    success: str
    success_bg: str
    success_border: str
    warning: str
    warning_bg: str
    warning_border: str
    error: str
    error_bg: str
    error_border: str
    info: str
    info_bg: str
    info_border: str


DARK_TOKENS = ThemeTokens(
    mode="dark",
    bg_base="#090A0F",
    bg_nav="#12141C",
    bg_surface="#181B26",
    bg_elevated="#1F2332",
    bg_hover="#262B3D",
    bg_input="#0D0F16",
    bg_hero="#131624",
    bg_selected="#231B3D",
    border="#242836",
    border_hover="#373E54",
    border_hero="#6D28D9",
    text_primary="#F3F4F6",
    text_secondary="#D1D5DB",
    text_muted="#9CA3AF",
    accent="#7C3AED",
    accent_hover="#8B5CF6",
    accent_pressed="#6D28D9",
    accent_text="#FFFFFF",
    accent_secondary="#06B6D4",
    accent_secondary_bg="#0B2530",
    accent_secondary_border="#0891B2",
    success="#10B981",
    success_bg="#0B2920",
    success_border="#059669",
    warning="#F59E0B",
    warning_bg="#2B1F0A",
    warning_border="#D97706",
    error="#EF4444",
    error_bg="#2C1216",
    error_border="#DC2626",
    info="#06B6D4",
    info_bg="#0B2530",
    info_border="#0E7490",
)

LIGHT_TOKENS = ThemeTokens(
    mode="light",
    bg_base="#F4F5F8",
    bg_nav="#FFFFFF",
    bg_surface="#FFFFFF",
    bg_elevated="#EEF1F6",
    bg_hover="#E5E9F2",
    bg_input="#F9FAFB",
    bg_hero="#F3F0FF",
    bg_selected="#EDE9FE",
    border="#CBD5E1",
    border_hover="#94A3B8",
    border_hero="#8B5CF6",
    text_primary="#0F172A",
    text_secondary="#334155",
    text_muted="#64748B",
    accent="#7C3AED",
    accent_hover="#6D28D9",
    accent_pressed="#5B21B6",
    accent_text="#FFFFFF",
    accent_secondary="#0891B2",
    accent_secondary_bg="#ECFEFF",
    accent_secondary_border="#06B6D4",
    success="#059669",
    success_bg="#ECFDF5",
    success_border="#6EE7B7",
    warning="#D97706",
    warning_bg="#FFFBEB",
    warning_border="#FCD34D",
    error="#DC2626",
    error_bg="#FEF2F2",
    error_border="#FCA5A5",
    info="#0284C7",
    info_bg="#F0F9FF",
    info_border="#7DD3FC",
)


def get_theme_tokens(mode: ThemeMode = "dark") -> ThemeTokens:
    return LIGHT_TOKENS if mode == "light" else DARK_TOKENS
