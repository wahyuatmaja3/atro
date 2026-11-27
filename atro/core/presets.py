"""Built-in viral clip presets and JSON persistence for ATRO Modular Template Composer."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from atro.config import get_app_base_dir
from atro.core.composer_schema import (
    AudioLayerConfig,
    ClipComposerConfig,
    ClipTimingConfig,
    ClimaxEffectsConfig,
    CommentBubbleItem,
    CropBoxConfig,
    DividerConfig,
    HookBannerConfig,
    LayoutLayerConfig,
    OverlayLayerConfig,
    SpeakerColorPalette,
    SubtitleFootnoteConfig,
    SubtitleLayerConfig,
    TranscriptSegmentOverride,
    WatermarkConfig,
)
from atro.core.schemas import TranscriptSegment
from atro.core.template_packs import (
    TEMPLATE_CATEGORIES,
    export_template_package,
    import_template_package,
    load_all_template_packs,
    recommend_templates_for_context,
    render_template_thumbnail_qimage,
)

BUILTIN_PRESET_IDS: tuple[str, ...] = (
    "podcast",
    "gaming_split",
    "twitch_comment",
    "subathon",
)

CAPTION_RECIPES: dict[str, dict[str, Any]] = {
    "hormozi_viral": {
        "label": "Hormozi Viral (Tebal + Dual Warna)",
        "description": "Teks tebal uppercase dengan sorotan kuning + keyword kedua hijau & auto-emoji.",
        "style_mode": "karaoke_highlight",
        "caption_bg_mode": "none",
        "active_word_bg_hex": "#F97316",
        "active_word_bg_radius": 18,
        "dual_keyword_enabled": True,
        "secondary_keyword_color_hex": "#22C55E",
        "auto_emoji_enabled": True,
        "font_size": 72,
        "primary_color_hex": "#FFFFFF",
        "highlight_color_hex": "#FACC15",
        "outline_color_hex": "#000000",
        "outline_width": 6.0,
        "shadow_depth": 3.0,
        "glow_strength": 0.0,
        "animation": "pop",
        "uppercase": True,
        "max_words_per_line": 3,
    },
    "mrbeast_pill": {
        "label": "MrBeast Active Pill (Kapsul Aktif)",
        "description": "Kapsul oranye bulat mengikuti kata yang terucap dengan animasi pop besar.",
        "style_mode": "karaoke_highlight",
        "caption_bg_mode": "active_word_pill",
        "active_word_bg_hex": "#F97316",
        "active_word_bg_radius": 20,
        "dual_keyword_enabled": False,
        "secondary_keyword_color_hex": "#FACC15",
        "auto_emoji_enabled": True,
        "font_size": 68,
        "primary_color_hex": "#FFFFFF",
        "highlight_color_hex": "#FFFFFF",
        "outline_color_hex": "#000000",
        "outline_width": 5.0,
        "shadow_depth": 3.0,
        "glow_strength": 0.0,
        "animation": "pop",
        "uppercase": True,
        "max_words_per_line": 3,
    },
    "lokaclip_neon": {
        "label": "LokaClip Neon (Ungu + Cyan Glow)",
        "description": "Gaya kreator modern: kapsul ungu neon, kata aktif biru elektrik & glow halus.",
        "style_mode": "karaoke_highlight",
        "caption_bg_mode": "active_word_pill",
        "active_word_bg_hex": "#7C3AED",
        "active_word_bg_radius": 18,
        "dual_keyword_enabled": True,
        "secondary_keyword_color_hex": "#4ADE80",
        "auto_emoji_enabled": True,
        "font_size": 66,
        "primary_color_hex": "#F8FAFC",
        "highlight_color_hex": "#38BDF8",
        "outline_color_hex": "#090D16",
        "outline_width": 4.5,
        "shadow_depth": 2.0,
        "glow_strength": 4.5,
        "animation": "pop",
        "uppercase": True,
        "max_words_per_line": 3,
    },
    "mageran_glass": {
        "label": "Mageran Glass Bar (Kotak Transparan)",
        "description": "Bar kaca gelap elegan di belakang seluruh baris teks untuk keterbacaan maksimal.",
        "style_mode": "podcast_3_4",
        "caption_bg_mode": "full_line_box",
        "active_word_bg_hex": "#CC111827",
        "active_word_bg_radius": 22,
        "dual_keyword_enabled": False,
        "secondary_keyword_color_hex": "#38BDF8",
        "auto_emoji_enabled": False,
        "font_size": 60,
        "primary_color_hex": "#F8FAFC",
        "highlight_color_hex": "#F97316",
        "outline_color_hex": "#000000",
        "outline_width": 3.5,
        "shadow_depth": 2.0,
        "glow_strength": 0.0,
        "animation": "karaoke",
        "uppercase": False,
        "max_words_per_line": 4,
    },
    "abdaal_minimal": {
        "label": "Ali Abdaal Minimal (Bersih & Rapi)",
        "description": "Huruf normal rapi khas edukasi/storytelling dengan dual sorotan warna kalem.",
        "style_mode": "podcast_3_4",
        "caption_bg_mode": "none",
        "active_word_bg_hex": "#F97316",
        "active_word_bg_radius": 16,
        "dual_keyword_enabled": True,
        "secondary_keyword_color_hex": "#38BDF8",
        "auto_emoji_enabled": False,
        "font_size": 58,
        "primary_color_hex": "#FFFFFF",
        "highlight_color_hex": "#FACC15",
        "outline_color_hex": "#0F172A",
        "outline_width": 4.0,
        "shadow_depth": 3.0,
        "glow_strength": 0.0,
        "animation": "scale",
        "uppercase": False,
        "max_words_per_line": 4,
    },
    "gamer_split": {
        "label": "Gamer Split (Esports Biru)",
        "description": "Kontras tinggi untuk split-screen gameplay: kapsul biru langit + keyword emas.",
        "style_mode": "karaoke_highlight",
        "caption_bg_mode": "active_word_pill",
        "active_word_bg_hex": "#0284C7",
        "active_word_bg_radius": 16,
        "dual_keyword_enabled": True,
        "secondary_keyword_color_hex": "#FACC15",
        "auto_emoji_enabled": True,
        "font_size": 64,
        "primary_color_hex": "#FFFFFF",
        "highlight_color_hex": "#FFFFFF",
        "outline_color_hex": "#000000",
        "outline_width": 5.5,
        "shadow_depth": 2.5,
        "glow_strength": 0.0,
        "animation": "pop",
        "uppercase": True,
        "max_words_per_line": 3,
    },
    "streamer_kinetic": {
        "label": "Streamer Kinetik (1-2 Kata Besar)",
        "description": "Klip reaksi cepat: 1-2 kata besar dengan kapsul ungu Twitch & emoji reaksi.",
        "style_mode": "kinetic_1_2",
        "caption_bg_mode": "active_word_pill",
        "active_word_bg_hex": "#9333EA",
        "active_word_bg_radius": 20,
        "dual_keyword_enabled": False,
        "secondary_keyword_color_hex": "#38BDF8",
        "auto_emoji_enabled": True,
        "font_size": 76,
        "primary_color_hex": "#FFFFFF",
        "highlight_color_hex": "#FFFFFF",
        "outline_color_hex": "#000000",
        "outline_width": 6.0,
        "shadow_depth": 3.0,
        "glow_strength": 0.0,
        "animation": "scale",
        "uppercase": True,
        "max_words_per_line": 2,
    },
    "podcast_editorial": {
        "label": "Podcast Editorial (Studio Kuning)",
        "description": "Bar semi-transparan dengan highlight kuning hangat & sorotan kata penting.",
        "style_mode": "podcast_3_4",
        "caption_bg_mode": "full_line_box",
        "active_word_bg_hex": "#B30F0F12",
        "active_word_bg_radius": 18,
        "dual_keyword_enabled": True,
        "secondary_keyword_color_hex": "#FB923C",
        "auto_emoji_enabled": False,
        "font_size": 62,
        "primary_color_hex": "#FFFFFF",
        "highlight_color_hex": "#FACC15",
        "outline_color_hex": "#000000",
        "outline_width": 4.5,
        "shadow_depth": 2.0,
        "glow_strength": 0.0,
        "animation": "karaoke",
        "uppercase": True,
        "max_words_per_line": 4,
    },
    "clipper_pop_kuning": {
        "label": "Clipper Pop Kuning (Shorts Viral)",
        "description": "Teks natural Impact putih + kata aktif membesar kuning emas (#FDD700) & outline hitam tebal ala Shorts clipper.",
        "style_mode": "karaoke_highlight",
        "caption_bg_mode": "none",
        "active_word_bg_hex": "#FDD700",
        "active_word_bg_radius": 18,
        "dual_keyword_enabled": False,
        "secondary_keyword_color_hex": "#FDD700",
        "auto_emoji_enabled": False,
        "font_family": "Impact",
        "font_size": 66,
        "primary_color_hex": "#FFFFFF",
        "highlight_color_hex": "#FDD700",
        "outline_color_hex": "#000000",
        "outline_width": 6.0,
        "shadow_depth": 3.0,
        "glow_strength": 0.0,
        "animation": "pop",
        "active_word_scale": 1.38,
        "uppercase": False,
        "max_words_per_line": 5,
    },
}


def apply_caption_recipe(config: ClipComposerConfig, recipe_id: str) -> ClipComposerConfig:
    """Apply a named caption recipe onto SubtitleLayerConfig while preserving anchors."""
    rid = recipe_id if recipe_id in CAPTION_RECIPES else "hormozi_viral"
    recipe = CAPTION_RECIPES[rid]
    sub = config.subtitles
    sub.caption_recipe = rid
    sub.active_word_scale = recipe.get("active_word_scale")
    for key, val in recipe.items():
        if key in ("label", "description"):
            continue
        if hasattr(sub, key):
            setattr(sub, key, val)
    return config


HOOK_STYLE_RECIPES: dict[str, dict[str, Any]] = {
    "viral_sticker": {
        "label": "Sticker Pop Kuning (Viral Short)",
        "description": "Impact miring kuning cerah + outline hitam tebal + bayangan 3D ungu & tepi stiker putih.",
        "design_style": "viral_sticker",
        "font_family": "Impact",
        "italic": True,
        "uppercase": True,
        "font_size": 74,
        "pos_y": 920,
        "text_color_hex": "#FFE600",
        "secondary_text_color_hex": "#FFFFFF",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#FFE600",
        "outline_color_hex": "#000000",
        "outline_width": 7.0,
        "shadow_color_hex": "#5B21B6",
        "shadow_depth": 7.5,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 6.5,
        "transition_in": "zoom",
    },
    "viral_two_tone": {
        "label": "Sticker 2-Warna (Putih + Kuning)",
        "description": "Baris atas putih bersih + baris bawah kuning cerah, outline hitam, 3D ungu & stiker putih.",
        "design_style": "viral_two_tone",
        "font_family": "Impact",
        "italic": True,
        "uppercase": True,
        "font_size": 74,
        "pos_y": 900,
        "text_color_hex": "#FFE600",
        "secondary_text_color_hex": "#FFFFFF",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#FFE600",
        "outline_color_hex": "#000000",
        "outline_width": 7.0,
        "shadow_color_hex": "#5B21B6",
        "shadow_depth": 7.5,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 6.5,
        "transition_in": "zoom",
    },
    "viral_white_red": {
        "label": "Sticker Drama Merah (Komedi/Hot)",
        "description": "Putih miring tegas + outline hitam tebal + bayangan 3D merah menyala & rim putih.",
        "design_style": "viral_white_red",
        "font_family": "Impact",
        "italic": True,
        "uppercase": True,
        "font_size": 74,
        "pos_y": 900,
        "text_color_hex": "#FFFFFF",
        "secondary_text_color_hex": "#FFE600",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#DC2626",
        "outline_color_hex": "#000000",
        "outline_width": 9.0,
        "shadow_color_hex": "#DC2626",
        "shadow_depth": 9.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 6.5,
        "transition_in": "whip",
    },
    "toxic_lime": {
        "label": "Sticker Hijau Neon (Gaming/Chaos)",
        "description": "Hijau lime miring mencolok + outline hitam pekat + shadow 3D ungu tua & stiker putih.",
        "design_style": "toxic_lime",
        "font_family": "Impact",
        "italic": True,
        "uppercase": True,
        "font_size": 72,
        "pos_y": 880,
        "text_color_hex": "#A3E635",
        "secondary_text_color_hex": "#FFFFFF",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#A3E635",
        "outline_color_hex": "#090D16",
        "outline_width": 8.5,
        "shadow_color_hex": "#4C1D95",
        "shadow_depth": 8.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 6.0,
        "transition_in": "zoom",
    },
    "cyan_arcade": {
        "label": "Sticker Cyan Elektrik (Gaming/Live)",
        "description": "Biru cyan terang + outline hitam pekat + shadow 3D biru cobalt & tepi stiker putih.",
        "design_style": "cyan_arcade",
        "font_family": "Impact",
        "italic": True,
        "uppercase": True,
        "font_size": 70,
        "pos_y": 780,
        "text_color_hex": "#38BDF8",
        "secondary_text_color_hex": "#FFFFFF",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#38BDF8",
        "outline_color_hex": "#090D16",
        "outline_width": 8.5,
        "shadow_color_hex": "#1D4ED8",
        "shadow_depth": 8.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 6.0,
        "transition_in": "zoom",
    },
    "neon_poster": {
        "label": "Poster Neon Glow (Ungu / Emas)",
        "description": "Huruf miring putih-ke-kuning dengan pendar cahaya neon ungu/biru kuat tanpa kotak.",
        "design_style": "neon_poster",
        "font_family": "Arial Black",
        "italic": True,
        "uppercase": True,
        "font_size": 68,
        "pos_y": 860,
        "text_color_hex": "#FFFFFF",
        "secondary_text_color_hex": "#FDE047",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#A855F7",
        "outline_color_hex": "#090D16",
        "outline_width": 8.0,
        "shadow_color_hex": "#9333EA",
        "shadow_depth": 12.0,
        "sticker_border_color_hex": "#38BDF8",
        "sticker_border_width": 0.0,
        "transition_in": "fade",
    },
    "floating_outline": {
        "label": "Minimalist Outlined (Podcast/Edukasi)",
        "description": "Teks tebal tegak bersih tanpa kotak dengan outline hitam tebal & bayangan lembut.",
        "design_style": "floating_outline",
        "font_family": "Arial Black",
        "italic": False,
        "uppercase": True,
        "font_size": 64,
        "pos_y": 420,
        "text_color_hex": "#FFFFFF",
        "secondary_text_color_hex": "#FACC15",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#FACC15",
        "outline_color_hex": "#000000",
        "outline_width": 8.5,
        "shadow_color_hex": "#0F172A",
        "shadow_depth": 7.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 0.0,
        "transition_in": "fade",
    },
    "gold_cinema": {
        "label": "Emas Sinematik (Story / Motivasi)",
        "description": "Gradasi emas ke amber dengan outline gelap & cahaya hangat sinematik.",
        "design_style": "gold_cinema",
        "font_family": "Arial Black",
        "italic": False,
        "uppercase": True,
        "font_size": 66,
        "pos_y": 840,
        "text_color_hex": "#FACC15",
        "secondary_text_color_hex": "#FEF9C3",
        "bg_color_hex": "#00000000",
        "accent_color_hex": "#F59E0B",
        "outline_color_hex": "#1C1917",
        "outline_width": 8.0,
        "shadow_color_hex": "#78350F",
        "shadow_depth": 10.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 0.0,
        "transition_in": "fade",
    },
    "slanted_pill": {
        "label": "Pita Highlight Kuning (Reels Pop)",
        "description": "Tiap baris dibungkus pita highlight miring warna kuning cerah dengan huruf hitam tegas.",
        "design_style": "slanted_pill",
        "font_family": "Impact",
        "italic": True,
        "uppercase": True,
        "font_size": 64,
        "pos_y": 820,
        "text_color_hex": "#111827",
        "secondary_text_color_hex": "#FFFFFF",
        "bg_color_hex": "#FFFACC15",
        "accent_color_hex": "#FACC15",
        "outline_color_hex": "#000000",
        "outline_width": 0.0,
        "shadow_color_hex": "#000000",
        "shadow_depth": 8.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 3.0,
        "transition_in": "whip",
    },
    "magenta_pop": {
        "label": "Pita Pink Neon (Gosip / Viral)",
        "description": "Pita miring pink neon mencolok bertepi putih dengan teks putih besar & shadow keras.",
        "design_style": "magenta_pop",
        "font_family": "Impact",
        "italic": True,
        "uppercase": True,
        "font_size": 64,
        "pos_y": 820,
        "text_color_hex": "#FFFFFF",
        "secondary_text_color_hex": "#FDE047",
        "bg_color_hex": "#FFEC4899",
        "accent_color_hex": "#EC4899",
        "outline_color_hex": "#4C1D95",
        "outline_width": 3.5,
        "shadow_color_hex": "#3B0764",
        "shadow_depth": 9.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 4.0,
        "transition_in": "slide",
    },
    "breaking_bar": {
        "label": "Headline Berita (Breaking Bar)",
        "description": "Badge topik merah di atas pita gelap tegas untuk klip berita, opini, atau debat panas.",
        "design_style": "breaking_bar",
        "font_family": "Arial Black",
        "italic": False,
        "uppercase": True,
        "font_size": 52,
        "pos_y": 320,
        "text_color_hex": "#FFFFFF",
        "secondary_text_color_hex": "#FACC15",
        "bg_color_hex": "#F0090D16",
        "accent_color_hex": "#DC2626",
        "outline_color_hex": "#000000",
        "outline_width": 0.0,
        "shadow_color_hex": "#000000",
        "shadow_depth": 8.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 0.0,
        "eyebrow_text": "HOT TOPIC",
        "transition_in": "slide",
    },
    "paper_label": {
        "label": "Label Kertas (Neo-Brutalist)",
        "description": "Pita kertas krem miring tipis bergaris tepi hitam pekat & bayangan keras modern.",
        "design_style": "paper_label",
        "font_family": "Arial Black",
        "italic": False,
        "uppercase": True,
        "font_size": 52,
        "pos_y": 340,
        "text_color_hex": "#111827",
        "secondary_text_color_hex": "#DC2626",
        "bg_color_hex": "#FFFFFDF5",
        "accent_color_hex": "#FACC15",
        "outline_color_hex": "#111827",
        "outline_width": 4.0,
        "shadow_color_hex": "#111827",
        "shadow_depth": 10.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 0.0,
        "transition_in": "zoom",
    },
    "dark_card": {
        "label": "Kartu Kaca Klasik (Podcast)",
        "description": "Kartu kotak gelap membulat dengan aksen garis atas.",
        "design_style": "dark_card",
        "font_family": "Segoe UI",
        "italic": False,
        "uppercase": False,
        "font_size": 34,
        "pos_y": 215,
        "text_color_hex": "#FFFFFF",
        "secondary_text_color_hex": "#FACC15",
        "bg_color_hex": "#E617110F",
        "accent_color_hex": "#FACC15",
        "outline_color_hex": "#000000",
        "outline_width": 0.0,
        "shadow_color_hex": "#000000",
        "shadow_depth": 0.0,
        "sticker_border_color_hex": "#FFFFFF",
        "sticker_border_width": 0.0,
        "transition_in": "fade",
    },
}


def apply_hook_style_recipe(
    config: ClipComposerConfig,
    style_id: str,
    preserve_anchor: bool = False,
) -> ClipComposerConfig:
    """Apply one of the 13 curated Hook Caption designs while keeping caption text intact."""
    sid = style_id if style_id in HOOK_STYLE_RECIPES else "viral_sticker"
    recipe = HOOK_STYLE_RECIPES[sid]
    hb = config.overlays.hook_banner
    saved_y = hb.pos_y
    for key, val in recipe.items():
        if key in ("label", "description"):
            continue
        if preserve_anchor and key == "pos_y":
            continue
        if hasattr(hb, key):
            setattr(hb, key, val)
    if preserve_anchor:
        hb.pos_y = saved_y
    return config


def generate_social_copywriting(
    clip_title: str,
    reason: str = "",
    preset_id: str = "podcast",
    platform_target: str = "youtube_shorts",
) -> dict[str, Any]:
    """Generate instant ready-to-post viral hook caption and hashtags strictly for YouTube Shorts & TikTok."""
    clean_title = (clip_title or "Momen Viral Hari Ini").strip()
    clean_reason = (reason or "").strip()
    pid = normalize_preset_id(preset_id)
    plat = "tiktok" if str(platform_target).strip().lower() == "tiktok" else "youtube_shorts"

    hook_prefix = {
        "podcast": "Plot twist di menit ini 🔥",
        "gaming_split": "Clutch paling gila yang wajib lu tonton 🎮",
        "twitch_comment": "Chat sampai gak bisa berhenti ketawa 😂",
        "subathon": "Momen Subathon paling chaos hari ini ⚡",
    }.get(pid, "Wajib tonton sampai habis 🔥")

    plat_tags = (
        ["#TikTok", "#TikTokIndonesia", "#FYP", "#ForYou"]
        if plat == "tiktok"
        else ["#Shorts", "#YouTubeShorts", "#PodcastShorts"]
    )
    base_tags = {
        "podcast": ["#PodcastClips", "#InsightHarian", "#Motivasi"] + plat_tags,
        "gaming_split": ["#GamingClips", "#GameplayID", "#ProPlayer"] + plat_tags,
        "twitch_comment": ["#StreamerID", "#TwitchClips", "#Ngakak"] + plat_tags,
        "subathon": ["#Subathon", "#StreamerIndo", "#ViralClips"] + plat_tags,
    }.get(pid, ["#ViralClips", "#PodcastIndo"] + plat_tags)

    title_words = re.findall(r"[A-Za-z0-9]{4,}", clean_title)
    custom_tags: list[str] = []
    seen = {t.lower() for t in base_tags}
    for word in title_words[:3]:
        tag = f"#{word.capitalize()}"
        if tag.lower() not in seen:
            seen.add(tag.lower())
            custom_tags.append(tag)

    all_tags = custom_tags + base_tags
    summary_line = f"\n\n{clean_reason}" if clean_reason and clean_reason.lower() != clean_title.lower() else ""
    caption_body = (
        f"{hook_prefix} — {clean_title}{summary_line}\n\n"
        f"Setuju sama poin ini? Tulis pendapatmu di kolom komentar 👇"
    )
    hashtags_str = " ".join(all_tags)
    return {
        "platform_target": plat,
        "hook_headline": f"{hook_prefix} — {clean_title}",
        "caption": caption_body,
        "hashtags": all_tags,
        "hashtags_text": hashtags_str,
        "full_post": f"{caption_body}\n\n{hashtags_str}",
    }


READY_TEMPLATES: dict[str, dict[str, Any]] = load_all_template_packs()


def reload_ready_templates(user_base_dir: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Reload dynamic JSON template packs (built-in + imported user .atrotpl) in-place."""
    fresh = load_all_template_packs(user_base_dir=user_base_dir)
    READY_TEMPLATES.clear()
    READY_TEMPLATES.update(fresh)
    return READY_TEMPLATES


def normalize_ready_template_id(template_id: str) -> str:
    """Normalize template IDs and common format aliases into READY_TEMPLATES keys."""
    cleaned = (template_id or "podcast").strip().lower()
    if cleaned == "game":
        return "gaming_split"
    if cleaned == "podcast_hormozi":
        return "podcast"
    if cleaned in READY_TEMPLATES:
        return cleaned
    return "podcast"


def build_ready_template_config(
    template_id: str,
    clip_title: str = "Judul Klip Viral",
    start_time: float = 0.0,
    end_time: float = 30.0,
    clip_index: int = 1,
    padding_sec: float = 0.4,
    backsound_dir: Optional[str] = None,
) -> ClipComposerConfig:
    """Build a complete all-in-one ClipComposerConfig from any READY_TEMPLATES entry."""
    tid = normalize_ready_template_id(template_id)
    meta = READY_TEMPLATES[tid]
    if meta.get("composer_override"):
        cfg = ClipComposerConfig.model_validate(meta["composer_override"])
        safe_start = max(0.0, float(start_time))
        safe_end = max(safe_start + 1.0, float(end_time))
        cfg.preset_id = tid
        cfg.preset_name = str(meta["title"])
        cfg.clip_index = clip_index
        cfg.clip_title = clip_title
        cfg.timing.start_time = safe_start
        cfg.timing.end_time = safe_end
        cfg.timing.padding_sec = padding_sec
        if backsound_dir is not None:
            cfg.audio.backsound_dir = backsound_dir
        cfg.overlays.hook_banner.text = clip_title
        if cfg.overlays.climax_effects.topic_header_enabled:
            cfg.overlays.climax_effects.topic_header_text = (clip_title or "HIGHLIGHT VIRAL").upper()[:42]
        cfg.sync_subtitle_anchor()
        return cfg

    cfg = build_preset_config(
        preset_id=str(meta["base_preset"]),
        clip_title=clip_title,
        start_time=start_time,
        end_time=end_time,
        clip_index=clip_index,
        padding_sec=padding_sec,
        backsound_dir=backsound_dir,
    )
    cfg.preset_id = tid
    cfg.preset_name = str(meta["title"])

    lay = cfg.layout
    lay.mode = meta["layout_mode"]
    if "split_ratio_top" in meta:
        lay.split_ratio_top = float(meta["split_ratio_top"])
    if "blurred_frame_aspect" in meta:
        lay.blurred_frame_aspect = meta["blurred_frame_aspect"]
    if "pip_shape" in meta:
        lay.pip_shape = meta["pip_shape"]
    if "pip_position" in meta:
        lay.pip_position = meta["pip_position"]
    if "pip_scale" in meta:
        lay.pip_scale = float(meta["pip_scale"])
    if "frame_corner_radius_px" in meta:
        lay.frame_corner_radius_px = int(meta["frame_corner_radius_px"])
    if "frame_border_width_px" in meta:
        lay.frame_border_width_px = int(meta["frame_border_width_px"])
    if "frame_border_color_hex" in meta:
        lay.frame_border_color_hex = str(meta["frame_border_color_hex"])
    if "divider_enabled" in meta:
        lay.divider.enabled = bool(meta["divider_enabled"])
    if "divider_angle_deg" in meta:
        lay.divider.angle_deg = float(meta["divider_angle_deg"])
    if "divider_color_hex" in meta:
        lay.divider.color_hex = str(meta["divider_color_hex"])
    if "focus_strategy" in meta:
        lay.focus_strategy = meta["focus_strategy"]
    if "group_mode" in meta:
        lay.group_mode = meta["group_mode"]
    if meta.get("transition_mode") in ("auto", "fixed"):
        lay.transition.mode = meta["transition_mode"]
    if "transition_type" in meta:
        lay.transition.type = meta["transition_type"]
    if "transition_sfx" in meta:
        lay.transition.sfx = meta["transition_sfx"]
    if meta.get("interactive_reaction_cuts") is not None:
        lay.interactive_reaction_cuts = bool(meta["interactive_reaction_cuts"])

    apply_caption_recipe(cfg, str(meta["caption_recipe"]))
    if meta.get("caption_bg_mode") in ("none", "active_word_pill", "full_line_box"):
        cfg.subtitles.caption_bg_mode = meta["caption_bg_mode"]
    if meta.get("max_words_per_line") is not None:
        cfg.subtitles.max_words_per_line = max(1, min(8, int(meta["max_words_per_line"])))
    if meta.get("auto_emoji_enabled") is not None:
        cfg.subtitles.auto_emoji_enabled = bool(meta["auto_emoji_enabled"])
    if meta.get("dual_keyword_enabled") is not None:
        cfg.subtitles.dual_keyword_enabled = bool(meta["dual_keyword_enabled"])
    if "font_family" in meta and meta["font_family"]:
        cfg.subtitles.font_family = str(meta["font_family"])
    if "highlight_color_hex" in meta:
        cfg.subtitles.highlight_color_hex = str(meta["highlight_color_hex"])
    if "secondary_keyword_color_hex" in meta:
        cfg.subtitles.secondary_keyword_color_hex = str(meta["secondary_keyword_color_hex"])
    if "active_word_bg_hex" in meta:
        cfg.subtitles.active_word_bg_hex = str(meta["active_word_bg_hex"])
    if meta.get("active_word_scale") is not None:
        cfg.subtitles.active_word_scale = max(1.0, min(1.80, float(meta["active_word_scale"])))
    cfg.subtitles.placement_preset = meta["placement_preset"]
    cfg.sync_subtitle_anchor()

    ov = cfg.overlays
    hook_style_id = meta.get("hook_design_style")
    if hook_style_id and str(hook_style_id) in HOOK_STYLE_RECIPES:
        apply_hook_style_recipe(cfg, str(hook_style_id))
    ov.hook_banner.enabled = bool(meta.get("hook_enabled", ov.hook_banner.enabled))
    if "hook_transition_in" in meta:
        ov.hook_banner.transition_in = meta["hook_transition_in"]
    if "hook_accent_hex" in meta:
        ov.hook_banner.accent_color_hex = str(meta["hook_accent_hex"])
    if meta.get("hook_font_size") is not None:
        ov.hook_banner.font_size = max(14, min(160, int(meta["hook_font_size"])))
    if meta.get("hook_pos_y") is not None:
        ov.hook_banner.pos_y = max(60, min(1750, int(meta["hook_pos_y"])))
    if meta.get("opening_tts_enabled") is not None:
        cfg.timing.opening_tts_enabled = bool(meta["opening_tts_enabled"])
    if meta.get("opening_intro_mode") in ("freeze_clear", "freeze_zoom", "freeze_blur"):
        ov.hook_banner.opening_intro_mode = meta["opening_intro_mode"]

    cl = ov.climax_effects
    cl.vignette_enabled = bool(meta.get("vignette_enabled", False))
    cl.zoom_punch_enabled = bool(meta.get("zoom_punch_enabled", False))
    if meta.get("zoom_punch_start_sec") is not None:
        cl.zoom_punch_start_sec = max(0.0, float(meta["zoom_punch_start_sec"]))
    if meta.get("zoom_punch_duration_sec") is not None:
        cl.zoom_punch_duration_sec = max(0.2, min(4.0, float(meta["zoom_punch_duration_sec"])))
    if meta.get("zoom_punch_factor") is not None:
        cl.zoom_punch_factor = max(1.02, min(1.5, float(meta["zoom_punch_factor"])))
    if meta.get("zoom_punch_repeat_sec") is not None:
        cl.zoom_punch_repeat_sec = max(0.0, min(30.0, float(meta["zoom_punch_repeat_sec"])))
    if meta.get("zoom_curve") in ("step", "smooth_in_out", "snap_out"):
        cl.zoom_curve = meta["zoom_curve"]
    if meta.get("zoom_sync_sfx") is not None:
        cl.zoom_sync_sfx = bool(meta["zoom_sync_sfx"])
    if meta.get("zoom_target") in ("center", "auto_context", "facecam", "gameplay"):
        cl.zoom_target = meta["zoom_target"]
    if meta.get("silence_trim_enabled") is not None:
        cfg.timing.silence_trim_enabled = bool(meta["silence_trim_enabled"])
    if meta.get("silence_max_gap_sec") is not None:
        cfg.timing.silence_max_gap_sec = max(0.10, min(2.0, float(meta["silence_max_gap_sec"])))
    if meta.get("contextual_sfx_enabled") is not None:
        cfg.audio.contextual_sfx_enabled = bool(meta["contextual_sfx_enabled"])
    if "progress_bar_color_hex" in meta:
        cl.progress_bar_color_hex = str(meta["progress_bar_color_hex"])
    topic_on = bool(meta.get("topic_header_enabled", False))
    cl.topic_header_enabled = topic_on
    if topic_on and not cl.topic_header_text.strip():
        cl.topic_header_text = (clip_title or "HIGHLIGHT VIRAL").upper()[:42]
    return cfg


def normalize_preset_id(preset_id: str) -> str:
    """Normalize format aliases such as 'game' -> 'gaming_split'."""
    cleaned = (preset_id or "podcast").strip().lower()
    if cleaned == "game":
        return "gaming_split"
    if cleaned in READY_TEMPLATES and cleaned not in BUILTIN_PRESET_IDS:
        return str(READY_TEMPLATES[cleaned]["base_preset"])
    if cleaned in BUILTIN_PRESET_IDS:
        return cleaned
    return "podcast"


def build_preset_config(
    preset_id: str,
    clip_title: str = "Judul Klip Viral",
    start_time: float = 0.0,
    end_time: float = 30.0,
    clip_index: int = 1,
    padding_sec: float = 0.4,
    backsound_dir: Optional[str] = None,
) -> ClipComposerConfig:
    """Construct a complete ClipComposerConfig initialized from built-in presets or ready templates."""
    raw_id = (preset_id or "podcast").strip().lower()
    if raw_id in READY_TEMPLATES and raw_id not in BUILTIN_PRESET_IDS:
        return build_ready_template_config(
            template_id=raw_id,
            clip_title=clip_title,
            start_time=start_time,
            end_time=end_time,
            clip_index=clip_index,
            padding_sec=padding_sec,
            backsound_dir=backsound_dir,
        )
    pid = normalize_preset_id(preset_id)
    safe_start = max(0.0, float(start_time))
    safe_end = max(safe_start + 1.0, float(end_time))
    timing = ClipTimingConfig(
        start_time=round(safe_start, 3),
        end_time=round(safe_end, 3),
        padding_sec=max(0.0, min(2.0, float(padding_sec))),
        opening_tts_enabled=False,
    )
    audio = AudioLayerConfig(
        backsound_enabled=True,
        backsound_dir=backsound_dir,
        selection_mode="rotate",
        backsound_volume=0.14,
        audio_ducking=True,
        vocal_loudnorm=True,
    )

    if pid == "gaming_split":
        cfg = ClipComposerConfig(
            preset_id="gaming_split",
            preset_name="Gaming Split (40:60)",
            clip_index=clip_index,
            clip_title=clip_title,
            timing=timing,
            layout=LayoutLayerConfig(
                mode="split_screen",
                split_ratio_top=0.40,
                framing_tracking_mode="auto_face",
                divider=DividerConfig(
                    enabled=True,
                    thickness_px=6,
                    color_hex="#38BDF8",
                    border_color_hex="#0B0F19",
                    angle_deg=0.0,
                    glow_enabled=True,
                ),
                top_crop=CropBoxConfig(
                    auto_detect=True,
                    x=0.04,
                    y=0.05,
                    w=0.34,
                    h=0.42,
                    label="Facecam / Subjek",
                ),
                bottom_crop=CropBoxConfig(
                    auto_detect=True,
                    x=0.22,
                    y=0.0,
                    w=0.56,
                    h=1.0,
                    label="Gameplay / Layar",
                ),
            ),
            subtitles=SubtitleLayerConfig(
                enabled=True,
                caption_recipe="gamer_split",
                style_mode="karaoke_highlight",
                caption_bg_mode="active_word_pill",
                active_word_bg_hex="#0284C7",
                active_word_bg_radius=16,
                dual_keyword_enabled=True,
                secondary_keyword_color_hex="#FACC15",
                auto_emoji_enabled=True,
                font_family="Arial Black",
                font_size=64,
                pos_x=540,
                pos_y=768,
                placement_preset="split_line_center",
                primary_color_hex="#FFFFFF",
                highlight_color_hex="#FFFFFF",
                outline_color_hex="#000000",
                outline_width=5.5,
                shadow_depth=2.5,
                animation="pop",
                uppercase=True,
                max_words_per_line=3,
            ),
            overlays=OverlayLayerConfig(
                hook_banner=HookBannerConfig(
                    enabled=True,
                    text=clip_title,
                    design_style="dark_card",
                    font_family="Segoe UI",
                    italic=False,
                    uppercase=False,
                    duration_sec=2.6,
                    bg_color_hex="#F2F8FAFC",
                    accent_color_hex="#38BDF8",
                    text_color_hex="#0F172A",
                    font_size=32,
                    pos_y=640,
                    transition_in="zoom",
                    transition_out="fade",
                ),
                comment_bubbles=[],
                watermark=WatermarkConfig(enabled=False),
                climax_effects=ClimaxEffectsConfig(
                    contrast_boost_enabled=True,
                    contrast=1.08,
                    saturation=1.18,
                    brightness=0.01,
                    zoom_punch_enabled=True,
                    zoom_punch_start_sec=max(1.5, round((safe_end - safe_start) * 0.55, 2)),
                    zoom_punch_duration_sec=1.6,
                    zoom_punch_factor=1.12,
                    progress_bar_enabled=True,
                    progress_bar_color_hex="#38BDF8",
                    vignette_enabled=True,
                ),
            ),
            audio=audio,
        )
        cfg.sync_subtitle_anchor()
        return cfg

    if pid == "twitch_comment":
        cfg = ClipComposerConfig(
            preset_id="twitch_comment",
            preset_name="Twitch Read Comment",
            clip_index=clip_index,
            clip_title=clip_title,
            timing=timing,
            layout=LayoutLayerConfig(
                mode="blurred_frame",
                split_ratio_top=0.50,
                framing_tracking_mode="manual_crop",
                blurred_frame_aspect="4:3",
                background_blur_strength=28,
                background_darken=-0.16,
                frame_corner_radius_px=34,
                frame_border_width_px=6,
                frame_border_color_hex="#A855F7",
                divider=DividerConfig(enabled=False),
                top_crop=CropBoxConfig(
                    auto_detect=True,
                    x=0.14,
                    y=0.02,
                    w=0.72,
                    h=0.96,
                    label="Streamer Center Frame",
                ),
                bottom_crop=CropBoxConfig(
                    auto_detect=False,
                    x=0.0,
                    y=0.0,
                    w=1.0,
                    h=1.0,
                    label="Full Backdrop",
                ),
            ),
            subtitles=SubtitleLayerConfig(
                enabled=True,
                caption_recipe="streamer_kinetic",
                style_mode="kinetic_1_2",
                caption_bg_mode="active_word_pill",
                active_word_bg_hex="#9333EA",
                active_word_bg_radius=20,
                auto_emoji_enabled=True,
                font_family="Arial Black",
                font_size=76,
                pos_x=540,
                pos_y=1152,
                placement_preset="bottom_safe",
                primary_color_hex="#FFFFFF",
                highlight_color_hex="#FFFFFF",
                outline_color_hex="#000000",
                outline_width=6.0,
                shadow_depth=3.0,
                animation="scale",
                uppercase=True,
                max_words_per_line=2,
            ),
            overlays=OverlayLayerConfig(
                hook_banner=HookBannerConfig(
                    enabled=False,
                    text=clip_title,
                    design_style="dark_card",
                    font_family="Segoe UI",
                    italic=False,
                    uppercase=False,
                    duration_sec=2.5,
                    bg_color_hex="#E618181B",
                    accent_color_hex="#9146FF",
                    text_color_hex="#FFFFFF",
                    font_size=32,
                    pos_y=210,
                    transition_in="slide",
                    transition_out="fade",
                ),
                comment_bubbles=[
                    CommentBubbleItem(
                        id="cb_1",
                        enabled=True,
                        author_label="viewers:",
                        username="chat_viewer",
                        text=clip_title or "Bang bahas momen ini dong!",
                        avatar_color_hex="#9146FF",
                        bg_color_hex="#F218181B",
                        text_color_hex="#F8FAFC",
                        start_sec=0.3,
                        end_sec=min(4.5, max(2.0, round(safe_end - safe_start, 2))),
                        pos_x=540,
                        pos_y=330,
                        scale=1.0,
                    )
                ],
                watermark=WatermarkConfig(enabled=False),
                climax_effects=ClimaxEffectsConfig(
                    contrast_boost_enabled=True,
                    contrast=1.05,
                    saturation=1.12,
                    brightness=0.01,
                    zoom_punch_enabled=False,
                    progress_bar_enabled=True,
                    progress_bar_color_hex="#9146FF",
                    vignette_enabled=True,
                ),
            ),
            audio=audio,
        )
        cfg.sync_subtitle_anchor()
        return cfg

    if pid == "subathon":
        cfg = ClipComposerConfig(
            preset_id="subathon",
            preset_name="Subathon Angled Split",
            clip_index=clip_index,
            clip_title=clip_title,
            timing=timing,
            layout=LayoutLayerConfig(
                mode="split_screen",
                split_ratio_top=0.50,
                framing_tracking_mode="manual_crop",
                divider=DividerConfig(
                    enabled=True,
                    thickness_px=8,
                    color_hex="#F97316",
                    border_color_hex="#090D16",
                    angle_deg=-7.0,
                    glow_enabled=True,
                ),
                top_crop=CropBoxConfig(
                    auto_detect=True,
                    x=0.04,
                    y=0.06,
                    w=0.40,
                    h=0.55,
                    label="Host / Cam 1",
                ),
                bottom_crop=CropBoxConfig(
                    auto_detect=True,
                    x=0.35,
                    y=0.08,
                    w=0.60,
                    h=0.84,
                    label="Screen / Cam 2",
                ),
            ),
            subtitles=SubtitleLayerConfig(
                enabled=True,
                caption_recipe="mrbeast_pill",
                style_mode="karaoke_highlight",
                caption_bg_mode="active_word_pill",
                active_word_bg_hex="#F97316",
                active_word_bg_radius=20,
                dual_keyword_enabled=True,
                secondary_keyword_color_hex="#FACC15",
                auto_emoji_enabled=True,
                font_family="Arial Black",
                font_size=68,
                pos_x=540,
                pos_y=960,
                placement_preset="split_line_center",
                primary_color_hex="#FFFFFF",
                highlight_color_hex="#FFFFFF",
                outline_color_hex="#000000",
                outline_width=5.5,
                shadow_depth=3.0,
                animation="pop",
                uppercase=True,
                max_words_per_line=3,
                multi_speaker_enabled=True,
                speaker_palettes={
                    "SPEAKER_1": SpeakerColorPalette(
                        primary_hex="#FFFFFF",
                        highlight_hex="#F97316",
                        outline_hex="#000000",
                    ),
                    "SPEAKER_2": SpeakerColorPalette(
                        primary_hex="#FEF08A",
                        highlight_hex="#38BDF8",
                        outline_hex="#0F172A",
                    ),
                    "ACTION": SpeakerColorPalette(
                        primary_hex="#FCA5A5",
                        highlight_hex="#EF4444",
                        outline_hex="#450A0A",
                    ),
                },
                action_token_color_hex="#FACC15",
                footnote=SubtitleFootnoteConfig(
                    enabled=True,
                    text="Subathon Highlight",
                    font_size=24,
                    color_hex="#E2E8F0",
                    offset_y=58,
                ),
            ),
            overlays=OverlayLayerConfig(
                hook_banner=HookBannerConfig(
                    enabled=True,
                    text=clip_title,
                    design_style="dark_card",
                    font_family="Segoe UI",
                    italic=False,
                    uppercase=False,
                    duration_sec=2.5,
                    bg_color_hex="#EB18181B",
                    accent_color_hex="#F97316",
                    text_color_hex="#FFFFFF",
                    font_size=32,
                    pos_y=210,
                    transition_in="whip",
                    transition_out="fade",
                ),
                comment_bubbles=[
                    CommentBubbleItem(
                        id="cb_1",
                        enabled=True,
                        author_label="viewers:",
                        username="subathon_chat",
                        text="NGAKAK PARAH INI!",
                        avatar_color_hex="#F97316",
                        bg_color_hex="#EB18181B",
                        text_color_hex="#F8FAFC",
                        start_sec=2.6,
                        end_sec=6.2,
                        pos_x=540,
                        pos_y=360,
                        scale=0.95,
                    )
                ],
                watermark=WatermarkConfig(
                    enabled=True,
                    text="@ATRO_LIVE",
                    position="top_right",
                    pos_x=915,
                    pos_y=84,
                    opacity=0.80,
                ),
                climax_effects=ClimaxEffectsConfig(
                    contrast_boost_enabled=True,
                    contrast=1.08,
                    saturation=1.16,
                    brightness=0.01,
                    zoom_punch_enabled=True,
                    zoom_punch_start_sec=max(1.5, round((safe_end - safe_start) * 0.5, 2)),
                    zoom_punch_duration_sec=1.5,
                    zoom_punch_factor=1.10,
                    progress_bar_enabled=True,
                    progress_bar_color_hex="#F97316",
                    vignette_enabled=True,
                    topic_header_enabled=True,
                    topic_header_text=clip_title.upper()[:42] or "SUBATHON HIGHLIGHT",
                ),
            ),
            audio=audio,
        )
        cfg.sync_subtitle_anchor()
        return cfg

    # Default: podcast
    cfg = ClipComposerConfig(
        preset_id="podcast",
        preset_name="Podcast Focus",
        clip_index=clip_index,
        clip_title=clip_title,
        timing=timing,
        layout=LayoutLayerConfig(
            mode="single_focus",
            split_ratio_top=0.50,
            framing_tracking_mode="auto_face",
            divider=DividerConfig(enabled=False),
            top_crop=CropBoxConfig(
                auto_detect=True,
                x=0.34,
                y=0.0,
                w=0.32,
                h=1.0,
                label="Active Speaker",
            ),
            bottom_crop=CropBoxConfig(
                auto_detect=True,
                x=0.55,
                y=0.0,
                w=0.32,
                h=1.0,
                label="Speaker 2",
            ),
        ),
        subtitles=SubtitleLayerConfig(
            enabled=True,
            caption_recipe="hormozi_viral",
            style_mode="podcast_3_4",
            caption_bg_mode="none",
            dual_keyword_enabled=True,
            secondary_keyword_color_hex="#22C55E",
            auto_emoji_enabled=True,
            font_family="Arial Black",
            font_size=66,
            pos_x=540,
            pos_y=1152,
            placement_preset="bottom_safe",
            primary_color_hex="#FFFFFF",
            highlight_color_hex="#FACC15",
            outline_color_hex="#000000",
            outline_width=5.5,
            shadow_depth=2.5,
            animation="karaoke",
            uppercase=True,
            max_words_per_line=4,
            multi_speaker_enabled=True,
        ),
        overlays=OverlayLayerConfig(
            hook_banner=HookBannerConfig(
                enabled=True,
                text=clip_title,
                design_style="dark_card",
                font_family="Segoe UI",
                italic=False,
                uppercase=False,
                duration_sec=2.8,
                bg_color_hex="#E617110F",
                accent_color_hex="#FACC15",
                text_color_hex="#FFFFFF",
                font_size=34,
                pos_y=215,
                transition_in="fade",
                transition_out="fade",
            ),
            comment_bubbles=[],
            watermark=WatermarkConfig(enabled=False),
            climax_effects=ClimaxEffectsConfig(
                contrast_boost_enabled=True,
                contrast=1.05,
                saturation=1.10,
                brightness=0.01,
                zoom_punch_enabled=False,
                progress_bar_enabled=True,
                progress_bar_color_hex="#FACC15",
                vignette_enabled=True,
            ),
        ),
        audio=audio,
    )
    cfg.sync_subtitle_anchor()
    return cfg


def get_presets_dir(base_dir: Optional[Path] = None) -> Path:
    """Return directory storing user custom JSON presets."""
    root = base_dir or get_app_base_dir()
    presets_dir = root / "presets"
    presets_dir.mkdir(parents=True, exist_ok=True)
    return presets_dir


def save_custom_preset(config: ClipComposerConfig, file_path: Path) -> Path:
    """Atomically save a ClipComposerConfig JSON preset file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(".json.tmp")
    tmp_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp_path, file_path)
    return file_path


def load_custom_preset(file_path: Path) -> ClipComposerConfig:
    """Load and validate a ClipComposerConfig JSON preset file."""
    return ClipComposerConfig.model_validate_json(file_path.read_text(encoding="utf-8"))


def save_job_clip_config(job_dir: Path, config: ClipComposerConfig) -> Path:
    """Persist per-clip composer config inside `<job_dir>/clip_configs/clip_XX.json`."""
    cfg_dir = job_dir / "clip_configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / f"clip_{config.clip_index:02d}.json"
    return save_custom_preset(config, target)


def extract_transcript_overrides(
    segments: Optional[list[TranscriptSegment]],
    start_time: float,
    end_time: float,
    padding_sec: float = 0.4,
) -> list[TranscriptSegmentOverride]:
    """Slice transcript segments overlapping `[start_time - padding, end_time + padding]`."""
    if not segments:
        return []
    win_start = max(0.0, start_time - padding_sec)
    win_end = end_time + padding_sec
    out: list[TranscriptSegmentOverride] = []
    for seg in segments:
        if seg.end <= win_start or seg.start >= win_end:
            continue
        out.append(
            TranscriptSegmentOverride(
                index=seg.index,
                start=seg.start,
                end=seg.end,
                text=seg.text,
                speaker="SPEAKER_1",
                words=list(seg.words),
            )
        )
    return out


def load_or_create_job_clip_config(
    job_dir: Path,
    clip_index: int,
    clip_title: str,
    start_time: float,
    end_time: float,
    edit_format: str = "podcast",
    padding_sec: float = 0.4,
    transcript_segments: Optional[list[TranscriptSegment]] = None,
    backsound_dir: Optional[str] = None,
) -> ClipComposerConfig:
    """Load saved per-clip config if present, otherwise initialize from preset."""
    cfg_path = job_dir / "clip_configs" / f"clip_{clip_index:02d}.json"
    if cfg_path.is_file():
        try:
            loaded = load_custom_preset(cfg_path)
            if not loaded.transcript_overrides and transcript_segments:
                loaded.transcript_overrides = extract_transcript_overrides(
                    transcript_segments,
                    loaded.timing.start_time,
                    loaded.timing.end_time,
                    loaded.timing.padding_sec,
                )
            return loaded
        except Exception:
            pass

    cfg = build_preset_config(
        preset_id=edit_format,
        clip_title=clip_title,
        start_time=start_time,
        end_time=end_time,
        clip_index=clip_index,
        padding_sec=padding_sec,
        backsound_dir=backsound_dir,
    )
    if transcript_segments:
        cfg.transcript_overrides = extract_transcript_overrides(
            transcript_segments,
            start_time,
            end_time,
            padding_sec,
        )
    return cfg
