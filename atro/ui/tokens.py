"""Centralized ATRO V2 Fluent Design Tokens (Dark & Light Themes + Spacing + Radii)."""

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
RADIUS_MD = 10
RADIUS_LG = 14


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    """Strongly typed Fluent color palette matching Dark and Light surfaces."""

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
    bg_base="#0F0E0D",
    bg_nav="#171514",
    bg_surface="#1E1B19",
    bg_elevated="#26221F",
    bg_hover="#312B27",
    bg_input="#151311",
    bg_hero="#1D1612",
    bg_selected="#2B1D14",
    border="#322C28",
    border_hover="#574A40",
    border_hero="#6B3B1D",
    text_primary="#FFFBEB",
    text_secondary="#E7E5E4",
    text_muted="#A8A29E",
    accent="#F97316",
    accent_hover="#EA580C",
    accent_pressed="#C2410C",
    accent_text="#FFFFFF",
    success="#10B981",
    success_bg="#0F2A22",
    success_border="#1B5E48",
    warning="#F59E0B",
    warning_bg="#2B1F11",
    warning_border="#9A3412",
    error="#EF4444",
    error_bg="#2A1616",
    error_border="#7F1D1D",
    info="#38BDF8",
    info_bg="#142333",
    info_border="#1D4E73",
)

LIGHT_TOKENS = ThemeTokens(
    mode="light",
    bg_base="#FAF8F5",
    bg_nav="#FFFFFF",
    bg_surface="#FFFFFF",
    bg_elevated="#F3EEE9",
    bg_hover="#EAE2DA",
    bg_input="#FFFFFF",
    bg_hero="#FFF7ED",
    bg_selected="#FFEDD5",
    border="#E5DDD5",
    border_hover="#CBBCAF",
    border_hero="#FDBA74",
    text_primary="#1C1917",
    text_secondary="#44403C",
    text_muted="#78716C",
    accent="#EA580C",
    accent_hover="#C2410C",
    accent_pressed="#9A3412",
    accent_text="#FFFFFF",
    success="#059669",
    success_bg="#ECFDF5",
    success_border="#A7F3D0",
    warning="#D97706",
    warning_bg="#FFFBEB",
    warning_border="#FDE68A",
    error="#DC2626",
    error_bg="#FEF2F2",
    error_border="#FECACA",
    info="#0284C7",
    info_bg="#F0F9FF",
    info_border="#BAE6FD",
)


def get_theme_tokens(mode: ThemeMode = "dark") -> ThemeTokens:
    """Return dark or light `ThemeTokens`."""
    return LIGHT_TOKENS if mode == "light" else DARK_TOKENS
