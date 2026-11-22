"""Dynamic JSON template pack registry, .atrotpl import/export, WYSIWYG thumbnails, and recommender."""

from __future__ import annotations

import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from atro.config import get_app_base_dir
from atro.core.composer_schema import ClipComposerConfig

TEMPLATE_CATEGORIES: tuple[str, ...] = (
    "Podcast",
    "Edukasi",
    "Bisnis",
    "Storytime",
    "Komedi",
    "Gaming",
    "Berita",
    "Religi",
    "Musik/Vlog",
)

# Seed catalog containing 25 curated V2 templates across 9 categories (exported to JSON packs on disk)
_SEED_TEMPLATE_SPECS: list[dict[str, Any]] = [
    # --- 1. PODCAST (5) ---
    {
        "id": "podcast",
        "category": "Podcast",
        "title": "Podcast Focus (Hormozi)",
        "badge": "1 Pembicara • Dual Warna",
        "description": "Fokus penuh 9:16 ke pembicara + Hook Banner pembuka + teks tebal dual warna & auto-emoji.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "hormozi_viral",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "vignette_enabled": True,
        "topic_header_enabled": False,
        "zoom_punch_enabled": False,
        "font_family": "Arial Black",
    },
    {
        "id": "mrbeast_energetic",
        "category": "Podcast",
        "title": "MrBeast Pill Energetik",
        "badge": "Kapsul Aktif • Zoom Klimaks",
        "description": "Kapsul oranye bergerak per kata, label topik atas, hook zoom cepat & zoom punch di klimaks.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "mrbeast_pill",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "zoom",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": True,
        "font_family": "Arial Black",
        "transition_mode": "auto",
        "transition_type": "zoom_in",
        "transition_sfx": "whoosh",
    },
    {
        "id": "podcast_mageran_glass",
        "category": "Podcast",
        "title": "Podcast Landscape + Bar Kaca",
        "badge": "Bingkai 16:9 Utuh • Glass Bar",
        "description": "Video horizontal 16:9 ditampilkan utuh bergaris emas + latar blur + bar kaca gelap ala Mageran.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "16:9",
        "frame_corner_radius_px": 28,
        "frame_border_width_px": 4,
        "frame_border_color_hex": "#FACC15",
        "caption_recipe": "mageran_glass",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Segoe UI",
        "transition_mode": "fixed",
        "transition_type": "crossfade",
        "transition_sfx": "none",
    },
    {
        "id": "podcast_duo_split",
        "category": "Podcast",
        "title": "Podcast Duel 50:50",
        "badge": "2 Pembicara • Dual Kamera",
        "description": "Membagi layar atas-bawah untuk dua narasumber dengan warna subtitle berbeda per pembicara.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "multi_speaker_split",
        "split_ratio_top": 0.50,
        "divider_enabled": True,
        "divider_angle_deg": 0.0,
        "divider_color_hex": "#F97316",
        "caption_recipe": "podcast_editorial",
        "placement_preset": "split_line_center",
        "hook_enabled": True,
        "hook_transition_in": "slide",
        "vignette_enabled": False,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Bahnschrift",
        "transition_mode": "fixed",
        "transition_type": "slide_left",
        "transition_sfx": "swipe",
    },
    {
        "id": "podcast_panel_5_orang",
        "category": "Podcast",
        "title": "Podcast Panel 5 Orang",
        "badge": "Auto POI • Multi-Kamera",
        "description": "Dirancang untuk podcast ramai (3-5 orang): pindah mulus mengikuti pembicara aktif & blur grup saat debat.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "abdaal_minimal",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Bahnschrift",
        "focus_strategy": "auto_topic",
        "group_mode": "fit_all_blur",
        "transition_mode": "auto",
        "transition_type": "slide_left",
        "transition_sfx": "whoosh",
    },
    # --- 2. EDUKASI / MOTIVASI (3) ---
    {
        "id": "edu_abdaal_clean",
        "category": "Edukasi",
        "title": "Edukasi Minimalist (Abdaal)",
        "badge": "Bersih • Font Rapi",
        "description": "Gaya Ali Abdaal: subtitle huruf normal bersih dengan sorotan kuning-biru tanpa menutup wajah.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "abdaal_minimal",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "slide",
        "vignette_enabled": False,
        "topic_header_enabled": False,
        "zoom_punch_enabled": False,
        "font_family": "Segoe UI",
        "transition_mode": "fixed",
        "transition_type": "fade",
        "transition_sfx": "none",
    },
    {
        "id": "edu_whiteboard_pip",
        "category": "Edukasi",
        "title": "Tutorial Layar + PiP Bulat",
        "badge": "PiP Penjelas • Bar Kaca",
        "description": "Menampilkan slide/tutorial penuh dengan wajah narator di sudut kanan atas & bar teks elegan.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "pip_facecam",
        "pip_shape": "circle",
        "pip_position": "top_right",
        "pip_scale": 0.30,
        "frame_corner_radius_px": 24,
        "frame_border_width_px": 4,
        "frame_border_color_hex": "#38BDF8",
        "caption_recipe": "mageran_glass",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "vignette_enabled": False,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Calibri",
        "transition_mode": "fixed",
        "transition_type": "crossfade",
        "transition_sfx": "none",
    },
    {
        "id": "edu_motivasi_sinematik",
        "category": "Edukasi",
        "title": "Motivasi Gelap & Emas",
        "badge": "Sinematik • Font Serif",
        "description": "Nuansa gelap dramatis dengan bingkai 4:3 bergaris emas hangat & font Georgia berwibawa.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "4:3",
        "frame_corner_radius_px": 24,
        "frame_border_width_px": 4,
        "frame_border_color_hex": "#FBBF24",
        "caption_recipe": "podcast_editorial",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Georgia",
        "highlight_color_hex": "#FBBF24",
        "transition_mode": "fixed",
        "transition_type": "dip_black",
        "transition_sfx": "none",
    },
    # --- 3. BISNIS / FINANSIAL (3) ---
    {
        "id": "biz_executive_noir",
        "category": "Bisnis",
        "title": "Bisnis Eksekutif Noir",
        "badge": "Profesional • Biru Cyan",
        "description": "Tampilan profesional untuk insight bisnis & strategi karier dengan header topik atas tegas.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "podcast_editorial",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "slide",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Bahnschrift",
        "highlight_color_hex": "#38BDF8",
        "hook_accent_hex": "#0EA5E9",
        "transition_mode": "fixed",
        "transition_type": "slide_right",
        "transition_sfx": "none",
    },
    {
        "id": "biz_crypto_chart",
        "category": "Bisnis",
        "title": "Saham & Crypto Split Neon",
        "badge": "Split Chart • Hijau Cuan",
        "description": "Analis di bagian atas & grafik saham/kripto di bawah dengan pembatas neon hijau menyala.",
        "base_preset": "gaming_split",
        "job_edit_format": "podcast",
        "layout_mode": "split_screen",
        "split_ratio_top": 0.38,
        "divider_enabled": True,
        "divider_angle_deg": 0.0,
        "divider_color_hex": "#10B981",
        "caption_recipe": "gamer_split",
        "placement_preset": "split_line_center",
        "hook_enabled": True,
        "hook_transition_in": "zoom",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": True,
        "font_family": "Arial Black",
        "active_word_bg_hex": "#059669",
        "progress_bar_color_hex": "#10B981",
        "transition_mode": "fixed",
        "transition_type": "wipe",
        "transition_sfx": "pop",
    },
    {
        "id": "biz_case_study_glass",
        "category": "Bisnis",
        "title": "Studi Kasus 16:9 Glass",
        "badge": "Presentasi • Glass Box",
        "description": "Menjaga chart presentasi 16:9 utuh di tengah layar berbingkai biru dengan teks bar kaca.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "16:9",
        "frame_corner_radius_px": 22,
        "frame_border_width_px": 4,
        "frame_border_color_hex": "#38BDF8",
        "caption_recipe": "mageran_glass",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Corbel",
        "transition_mode": "fixed",
        "transition_type": "fade",
        "transition_sfx": "none",
    },
    # --- 4. STORYTIME / DRAMA (3) ---
    {
        "id": "story_drama_zoom",
        "category": "Storytime",
        "title": "Storytime Plot Twist",
        "badge": "Banner Merah • Zoom In",
        "description": "Hook banner merah mencolok + kapsul neon + zoom cepat untuk cerita tegang & plot twist.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "lokaclip_neon",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "zoom",
        "hook_accent_hex": "#EF4444",
        "vignette_enabled": True,
        "topic_header_enabled": False,
        "zoom_punch_enabled": True,
        "font_family": "Arial Black",
        "transition_mode": "fixed",
        "transition_type": "zoom_in",
        "transition_sfx": "whoosh",
    },
    {
        "id": "story_confession_blur",
        "category": "Storytime",
        "title": "Curhat Malam & Sub Kuning",
        "badge": "Moody Gelap • Intim",
        "description": "Vignette gelap hangat dengan bingkai 4:3 lembut & subtitle editorial kuning gading.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "4:3",
        "frame_corner_radius_px": 30,
        "frame_border_width_px": 3,
        "frame_border_color_hex": "#FDE047",
        "caption_recipe": "podcast_editorial",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "vignette_enabled": True,
        "topic_header_enabled": False,
        "zoom_punch_enabled": False,
        "font_family": "Candara",
        "highlight_color_hex": "#FDE047",
        "transition_mode": "fixed",
        "transition_type": "crossfade",
        "transition_sfx": "none",
    },
    {
        "id": "story_timeline_pip",
        "category": "Storytime",
        "title": "Cerita + PiP Bukti Layar",
        "badge": "PiP Bawah • Narasi Rapi",
        "description": "Screenshot chat/video bukti sebagai layar utama + pencerita muncul di PiP kanan bawah.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "pip_facecam",
        "pip_shape": "rounded_rect",
        "pip_position": "bottom_right",
        "pip_scale": 0.30,
        "frame_corner_radius_px": 22,
        "frame_border_width_px": 4,
        "frame_border_color_hex": "#F97316",
        "caption_recipe": "abdaal_minimal",
        "placement_preset": "top_safe",
        "hook_enabled": True,
        "hook_transition_in": "slide",
        "vignette_enabled": False,
        "topic_header_enabled": False,
        "zoom_punch_enabled": False,
        "font_family": "Segoe UI",
        "transition_mode": "fixed",
        "transition_type": "slide_left",
        "transition_sfx": "swipe",
    },
    # --- 5. KOMEDI / MEME (2) ---
    {
        "id": "comedy_punch_whip",
        "category": "Komedi",
        "title": "Komedi Whip & Pop",
        "badge": "1-2 Kata Besar • Whip Pan",
        "description": "Potongan cepat berenergi dengan efek whip pan, 1-2 kata Impact raksasa & SFX Pop!",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "streamer_kinetic",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "whip",
        "vignette_enabled": False,
        "topic_header_enabled": True,
        "zoom_punch_enabled": True,
        "font_family": "Impact",
        "active_word_bg_hex": "#F59E0B",
        "progress_bar_color_hex": "#FACC15",
        "transition_mode": "fixed",
        "transition_type": "whip",
        "transition_sfx": "pop",
    },
    {
        "id": "comedy_duet_miring",
        "category": "Komedi",
        "title": "Reaksi Kocak Split Miring",
        "badge": "Garis -6° • Glitch Cut",
        "description": "Video reaksi split miring energik dengan garis oranye menyala & transisi glitch kocak.",
        "base_preset": "subathon",
        "job_edit_format": "subathon",
        "layout_mode": "split_screen",
        "split_ratio_top": 0.45,
        "divider_enabled": True,
        "divider_angle_deg": -6.0,
        "divider_color_hex": "#F97316",
        "caption_recipe": "mrbeast_pill",
        "placement_preset": "split_line_center",
        "hook_enabled": True,
        "hook_transition_in": "whip",
        "vignette_enabled": False,
        "topic_header_enabled": True,
        "zoom_punch_enabled": True,
        "font_family": "Impact",
        "transition_mode": "fixed",
        "transition_type": "glitch",
        "transition_sfx": "whoosh",
    },
    # --- 6. GAMING (3) ---
    {
        "id": "gaming_split",
        "category": "Gaming",
        "title": "Gaming Split Esports (40:60)",
        "badge": "Split Atas-Bawah • Garis Tengah",
        "description": "40% wajah atas & 60% gameplay bawah + garis pembatas neon biru + subtitle tepat di tengah.",
        "base_preset": "gaming_split",
        "job_edit_format": "game",
        "layout_mode": "split_screen",
        "split_ratio_top": 0.40,
        "divider_enabled": True,
        "divider_angle_deg": 0.0,
        "caption_recipe": "gamer_split",
        "placement_preset": "split_line_center",
        "hook_enabled": True,
        "hook_transition_in": "zoom",
        "vignette_enabled": True,
        "topic_header_enabled": False,
        "zoom_punch_enabled": True,
        "font_family": "Arial Black",
    },
    {
        "id": "pip_facecam_bulat",
        "category": "Gaming",
        "title": "Gameplay + PiP Facecam Bulat",
        "badge": "PiP Lingkaran • Neon Orange",
        "description": "Gameplay full-screen 9:16 dengan facecam bulat pojok kanan atas bergaris oranye + kapsul aktif.",
        "base_preset": "gaming_split",
        "job_edit_format": "game",
        "layout_mode": "pip_facecam",
        "pip_shape": "circle",
        "pip_position": "top_right",
        "pip_scale": 0.34,
        "frame_corner_radius_px": 28,
        "frame_border_width_px": 6,
        "frame_border_color_hex": "#F97316",
        "caption_recipe": "mrbeast_pill",
        "placement_preset": "bottom_safe",
        "hook_enabled": False,
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": True,
        "font_family": "Arial Black",
    },
    {
        "id": "subathon",
        "category": "Gaming",
        "title": "Subathon Split Miring (-7°)",
        "badge": "Garis Miring • Full Overlay",
        "description": "Split diagonal ikonik -7° dengan Hook Whip cepat, chat bubble, header topik & watermark live.",
        "base_preset": "subathon",
        "job_edit_format": "subathon",
        "layout_mode": "split_screen",
        "split_ratio_top": 0.50,
        "divider_enabled": True,
        "divider_angle_deg": -7.0,
        "caption_recipe": "mrbeast_pill",
        "placement_preset": "split_line_center",
        "hook_enabled": True,
        "hook_transition_in": "whip",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": True,
        "font_family": "Arial Black",
    },
    # --- 7. STREAMER & BERITA (3) ---
    {
        "id": "twitch_comment",
        "category": "Berita",
        "title": "Streamer Baca Komentar Chat",
        "badge": "Bingkai Blur • Bubble Chat",
        "description": "Video tengah bersudut membulat + latar belakang blur + kartu pertanyaan chat + teks kinetik.",
        "base_preset": "twitch_comment",
        "job_edit_format": "twitch_comment",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "4:3",
        "frame_corner_radius_px": 34,
        "frame_border_width_px": 6,
        "frame_border_color_hex": "#A855F7",
        "caption_recipe": "streamer_kinetic",
        "placement_preset": "bottom_safe",
        "hook_enabled": False,
        "vignette_enabled": True,
        "topic_header_enabled": False,
        "zoom_punch_enabled": False,
        "font_family": "Arial Black",
    },
    {
        "id": "pip_facecam_kotak",
        "category": "Berita",
        "title": "React / Tutorial + PiP Kotak Neon",
        "badge": "PiP Kotak • LokaClip Glow",
        "description": "Layar presentasi/reaksi penuh + kamera kotak membulat garis ungu neon + subtitle glow kekinian.",
        "base_preset": "gaming_split",
        "job_edit_format": "game",
        "layout_mode": "pip_facecam",
        "pip_shape": "rounded_rect",
        "pip_position": "top_left",
        "pip_scale": 0.34,
        "frame_corner_radius_px": 28,
        "frame_border_width_px": 6,
        "frame_border_color_hex": "#A855F7",
        "caption_recipe": "lokaclip_neon",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "slide",
        "vignette_enabled": True,
        "topic_header_enabled": False,
        "zoom_punch_enabled": False,
        "font_family": "Arial Black",
    },
    {
        "id": "news_breaking_bar",
        "category": "Berita",
        "title": "Breaking News / Info Viral",
        "badge": "16:9 Tegas • Merah Breaking",
        "description": "Cuplikan berita atau fakta cepat berbingkai merah tegas + label info atas & transisi wipe.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "16:9",
        "frame_corner_radius_px": 18,
        "frame_border_width_px": 5,
        "frame_border_color_hex": "#DC2626",
        "caption_recipe": "podcast_editorial",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "slide",
        "hook_accent_hex": "#DC2626",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Bahnschrift",
        "progress_bar_color_hex": "#EF4444",
        "transition_mode": "fixed",
        "transition_type": "wipe",
        "transition_sfx": "swipe",
    },
    # --- 8. RELIGI / TENANG (1) ---
    {
        "id": "religi_tausiyah_kalem",
        "category": "Religi",
        "title": "Kajian & Tausiyah Kalem",
        "badge": "Lembut • Hijau Zamrud",
        "description": "Tampilan teduh & rapi untuk ceramah/refleksi dengan font halus, tanpa emoji berlebihan, crossfade pelan.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "single_focus",
        "caption_recipe": "abdaal_minimal",
        "placement_preset": "bottom_safe",
        "hook_enabled": True,
        "hook_transition_in": "fade",
        "hook_accent_hex": "#10B981",
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": False,
        "font_family": "Georgia",
        "highlight_color_hex": "#6EE7B7",
        "secondary_keyword_color_hex": "#FDE68A",
        "progress_bar_color_hex": "#10B981",
        "transition_mode": "fixed",
        "transition_type": "crossfade",
        "transition_sfx": "none",
    },
    # --- 9. MUSIK / VLOG (2) ---
    {
        "id": "vlog_aesthetic_blur",
        "category": "Musik/Vlog",
        "title": "Daily Vlog Estetik Hangat",
        "badge": "4:3 Krem • Bersih",
        "description": "Gaya vlog estetik dengan bingkai 4:3 krem lembut, huruf Candara ringan & fade santai.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "4:3",
        "frame_corner_radius_px": 32,
        "frame_border_width_px": 4,
        "frame_border_color_hex": "#FDE68A",
        "caption_recipe": "mageran_glass",
        "placement_preset": "bottom_safe",
        "hook_enabled": False,
        "vignette_enabled": False,
        "topic_header_enabled": False,
        "zoom_punch_enabled": False,
        "font_family": "Candara",
        "progress_bar_color_hex": "#FDE68A",
        "transition_mode": "fixed",
        "transition_type": "fade",
        "transition_sfx": "none",
    },
    {
        "id": "musik_lirik_neon",
        "category": "Musik/Vlog",
        "title": "Musik / Lirik Beat Neon",
        "badge": "1:1 Kotak • Cyan Glow",
        "description": "Bingkai kotak 1:1 bersinar cyan + subtitle LokaClip glowing untuk klip lagu, cover & festival.",
        "base_preset": "podcast",
        "job_edit_format": "podcast",
        "layout_mode": "blurred_frame",
        "blurred_frame_aspect": "1:1",
        "frame_corner_radius_px": 30,
        "frame_border_width_px": 6,
        "frame_border_color_hex": "#06B6D4",
        "caption_recipe": "lokaclip_neon",
        "placement_preset": "bottom_safe",
        "hook_enabled": False,
        "vignette_enabled": True,
        "topic_header_enabled": True,
        "zoom_punch_enabled": True,
        "font_family": "Century Gothic",
        "progress_bar_color_hex": "#06B6D4",
        "transition_mode": "fixed",
        "transition_type": "blur_swap",
        "transition_sfx": "whoosh",
    },
]


class TemplatePackEntry(BaseModel):
    """Schema of each JSON file inside `atro/assets/templates/*.json` or user templates dir."""

    id: str
    category: str = "Podcast"
    title: str
    badge: str = "Template Studio"
    description: str = ""
    base_preset: str = "podcast"
    job_edit_format: str = "podcast"
    layout_mode: str = "single_focus"
    split_ratio_top: Optional[float] = None
    blurred_frame_aspect: Optional[str] = None
    pip_shape: Optional[str] = None
    pip_position: Optional[str] = None
    pip_scale: Optional[float] = None
    frame_corner_radius_px: Optional[int] = None
    frame_border_width_px: Optional[int] = None
    frame_border_color_hex: Optional[str] = None
    divider_enabled: Optional[bool] = None
    divider_angle_deg: Optional[float] = None
    divider_color_hex: Optional[str] = None
    caption_recipe: str = "hormozi_viral"
    placement_preset: str = "bottom_safe"
    hook_enabled: bool = True
    hook_transition_in: Optional[str] = None
    hook_accent_hex: Optional[str] = None
    vignette_enabled: bool = True
    topic_header_enabled: bool = False
    zoom_punch_enabled: bool = False
    font_family: Optional[str] = None
    highlight_color_hex: Optional[str] = None
    secondary_keyword_color_hex: Optional[str] = None
    active_word_bg_hex: Optional[str] = None
    progress_bar_color_hex: Optional[str] = None
    focus_strategy: Optional[str] = None
    group_mode: Optional[str] = None
    transition_mode: Optional[str] = None
    transition_type: Optional[str] = None
    transition_sfx: Optional[str] = None
    # Optional full composer snapshot used by exported user `.atrotpl` packs
    composer_override: Optional[dict[str, Any]] = None


def get_builtin_templates_dir() -> Path:
    """Resolve `atro/assets/templates` both in dev source tree and PyInstaller `_MEIPASS`."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "atro" / "assets" / "templates"
        if cand.is_dir():
            return cand
    return Path(__file__).resolve().parents[1] / "assets" / "templates"


def get_user_templates_dir(base_dir: Optional[Path] = None) -> Path:
    """Return writable user directory for imported `.atrotpl` / custom template JSON packs."""
    root = base_dir or get_app_base_dir()
    tdir = root / "templates"
    tdir.mkdir(parents=True, exist_ok=True)
    return tdir


def ensure_builtin_template_packs(target_dir: Optional[Path] = None) -> Path:
    """Write any missing built-in JSON template packs cleanly to `atro/assets/templates/`."""
    out_dir = target_dir or get_builtin_templates_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    for spec in _SEED_TEMPLATE_SPECS:
        dest = out_dir / f"{spec['id']}.json"
        if not dest.is_file():
            entry = TemplatePackEntry.model_validate(spec)
            dest.write_text(entry.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    return out_dir


def load_all_template_packs(user_base_dir: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Load all built-in + user JSON template packs preserving curated seed order first."""
    packs: dict[str, dict[str, Any]] = {}
    builtin_dir = ensure_builtin_template_packs()

    seed_order = [s["id"] for s in _SEED_TEMPLATE_SPECS]
    raw_by_id: dict[str, TemplatePackEntry] = {}

    for directory in (builtin_dir, get_user_templates_dir(user_base_dir)):
        if not directory.is_dir():
            continue
        for json_file in sorted(directory.glob("*.json")):
            try:
                entry = TemplatePackEntry.model_validate_json(json_file.read_text(encoding="utf-8"))
                raw_by_id[entry.id] = entry
            except Exception:
                continue

    for tid in seed_order:
        if tid in raw_by_id:
            packs[tid] = raw_by_id.pop(tid).model_dump(exclude_none=True)
    for tid in sorted(raw_by_id.keys()):
        packs[tid] = raw_by_id[tid].model_dump(exclude_none=True)

    return packs


def recommend_templates_for_context(
    title: str = "",
    transcript_text: str = "",
    edit_format: str = "podcast",
    packs: Optional[dict[str, dict[str, Any]]] = None,
) -> list[str]:
    """Recommend top 3 relevant template IDs from video topic, transcript text, and format."""
    haystack = f"{title} {transcript_text[:1500]}".lower()
    if packs is None:
        packs = load_all_template_packs()

    recs: list[str] = []
    kw_rules: list[tuple[tuple[str, ...], list[str]]] = [
        (("game", "gameplay", "clutch", "ranked", "valorant", "mobile legends", "ff", "pubg", "subathon"), ["gaming_split", "pip_facecam_bulat", "subathon"]),
        (("saham", "crypto", "kripto", "bitcoin", "bisnis", "omzet", "cuan", "investasi", "uang", "marketing"), ["biz_crypto_chart", "biz_executive_noir", "biz_case_study_glass"]),
        (("ustadz", "kajian", "tausiyah", "doa", "alquran", "shalat", "hijrah", "pahala", "surga"), ["religi_tausiyah_kalem", "edu_motivasi_sinematik"]),
        (("ngakak", "kocak", "prank", "wkwk", "haha", "lucu", "standup", "komedi"), ["comedy_punch_whip", "comedy_duet_miring"]),
        (("curhat", "cerita", "horor", "misteri", "plot twist", "selingkuh", "drama"), ["story_drama_zoom", "story_confession_blur"]),
        (("belajar", "tips", "cara", "tutorial", "produktivitas", "coding", "edukasi"), ["edu_abdaal_clean", "edu_whiteboard_pip"]),
        (("berita", "breaking", "viral", "heboh", "trending", "kasus"), ["news_breaking_bar", "podcast_mageran_glass"]),
        (("lagu", "lirik", "musik", "vlog", "travel", "konser", "cover"), ["musik_lirik_neon", "vlog_aesthetic_blur"]),
        (("podcast", "host", "narasumber", "tamu", "diskusi", "ngobrol", "bintang tamu"), ["podcast_panel_5_orang", "podcast", "podcast_duo_split"]),
    ]

    for keywords, cands in kw_rules:
        if any(kw in haystack for kw in keywords):
            for cid in cands:
                if cid in packs and cid not in recs:
                    recs.append(cid)

    if not recs:
        if edit_format == "game":
            recs = ["gaming_split", "pip_facecam_bulat", "subathon"]
        elif edit_format == "twitch_comment":
            recs = ["twitch_comment", "pip_facecam_kotak", "comedy_punch_whip"]
        elif edit_format == "subathon":
            recs = ["subathon", "comedy_duet_miring", "mrbeast_energetic"]
        else:
            recs = ["podcast_panel_5_orang", "podcast", "mrbeast_energetic"]

    return [r for r in recs if r in packs][:3]


def export_template_package(
    config: ClipComposerConfig,
    dest_path: Path,
    title: str = "",
    category: str = "Podcast",
    badge: str = "Template Kustom",
    description: str = "Paket template Studio siap pakai.",
) -> Path:
    """Bundle any `ClipComposerConfig` into a shareable `.atrotpl` ZIP archive."""
    clean_dest = dest_path if dest_path.suffix.lower() == ".atrotpl" else dest_path.with_suffix(".atrotpl")
    clean_dest.parent.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9_]+", "_", (title or config.preset_name or "custom_tpl").lower()).strip("_") or "custom_tpl"

    snap = config.model_copy(deep=True)
    # Strip clip-specific video timing/crops before sharing as reusable template
    snap.layout.shot_crops = []
    snap.transcript_overrides = []
    entry = TemplatePackEntry(
        id=f"custom_{slug}",
        category=category if category in TEMPLATE_CATEGORIES else "Podcast",
        title=title.strip() or snap.preset_name or "Custom Template",
        badge=badge.strip() or "Template Kustom",
        description=description.strip() or "Diimpor dari paket .atrotpl",
        base_preset="podcast",
        job_edit_format="podcast",
        layout_mode=snap.layout.mode,
        caption_recipe=snap.subtitles.caption_recipe,
        placement_preset=snap.subtitles.placement_preset,
        hook_enabled=snap.overlays.hook_banner.enabled,
        vignette_enabled=snap.overlays.climax_effects.vignette_enabled,
        topic_header_enabled=snap.overlays.climax_effects.topic_header_enabled,
        zoom_punch_enabled=snap.overlays.climax_effects.zoom_punch_enabled,
        font_family=snap.subtitles.font_family,
        composer_override=snap.model_dump(mode="json"),
    )

    tmp = clean_dest.with_suffix(".atrotpl.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("template.json", entry.model_dump_json(indent=2, exclude_none=True))
    os.replace(tmp, clean_dest)
    return clean_dest


def import_template_package(
    atrotpl_path: Path,
    user_base_dir: Optional[Path] = None,
) -> str:
    """Safely extract `template.json` from `.atrotpl` ZIP into user templates folder and return its ID."""
    if not atrotpl_path.is_file():
        raise FileNotFoundError(f"File paket template tidak ditemukan: {atrotpl_path}")

    with zipfile.ZipFile(atrotpl_path, "r") as zf:
        names = [n for n in zf.namelist() if not n.startswith("/") and ".." not in Path(n).parts]
        target_member = next((n for n in names if Path(n).name == "template.json"), None)
        if target_member is None:
            raise ValueError("Paket .atrotpl tidak berisi file template.json yang valid.")
        raw = zf.read(target_member).decode("utf-8")

    entry = TemplatePackEntry.model_validate_json(raw)
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", entry.id).strip("_") or "imported_tpl"
    entry.id = safe_id
    out_dir = get_user_templates_dir(user_base_dir)
    out_file = out_dir / f"{safe_id}.json"
    tmp = out_file.with_suffix(".json.tmp")
    tmp.write_text(entry.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    os.replace(tmp, out_file)
    return safe_id


_THUMB_MEMORY_CACHE: dict[str, Any] = {}


def render_template_thumbnail_qimage(
    template_id: str,
    width: int = 135,
    height: int = 240,
) -> Any:
    """Render & cache a miniature 1:1 WYSIWYG QImage thumbnail for any template ID."""
    cache_key = f"{template_id}:{width}x{height}"
    if cache_key in _THUMB_MEMORY_CACHE:
        return _THUMB_MEMORY_CACHE[cache_key]

    import cv2
    import numpy as np
    from PyQt6.QtCore import Qt

    from atro.core.presets import build_ready_template_config
    from atro.core.schemas import TranscriptSegment, TranscriptWord
    from atro.modules.cutter.composer_renderer import ensure_qt_gui_app, render_preview_frame

    ensure_qt_gui_app()

    # Crisp synthetic 1280x720 dual-speaker backdrop so single, blurred_frame, split & pip look distinct
    w_src, h_src = 640, 360
    bg = np.zeros((h_src, w_src, 3), dtype=np.uint8)
    for y in range(h_src):
        bg[y, :, 0] = 28 + (y * 18 // h_src)
        bg[y, :, 1] = 32 + (y * 20 // h_src)
        bg[y, :, 2] = 48
    cv2.rectangle(bg, (32, 30), (290, 330), (48, 58, 92), -1)
    cv2.circle(bg, (160, 145), 44, (110, 168, 232), -1)
    cv2.ellipse(bg, (160, 265), (72, 55), 0, 180, 360, (82, 132, 210), -1)
    cv2.rectangle(bg, (340, 30), (608, 330), (38, 78, 66), -1)
    cv2.circle(bg, (475, 145), 44, (104, 210, 168), -1)
    cv2.ellipse(bg, (475, 265), (72, 55), 0, 180, 360, (70, 168, 132), -1)

    cfg = build_ready_template_config(template_id, clip_title="Rahasia Cuan Viral", start_time=0.0, end_time=10.0)
    # Render on 540x960 for blazing-fast thumbnail generation while keeping exact relative geometry
    cfg.canvas_width, cfg.canvas_height = 540, 960
    cfg.subtitles.font_size = max(18, int(round(cfg.subtitles.font_size * 0.5)))
    cfg.subtitles.pos_x = int(round(cfg.subtitles.pos_x * 0.5))
    cfg.subtitles.pos_y = int(round(cfg.subtitles.pos_y * 0.5))
    cfg.overlays.hook_banner.font_size = max(12, int(round(cfg.overlays.hook_banner.font_size * 0.5)))
    cfg.overlays.hook_banner.pos_y = int(round(cfg.overlays.hook_banner.pos_y * 0.5))
    cfg.overlays.climax_effects.progress_bar_enabled = True

    segs = [
        TranscriptSegment(
            index=0,
            start=0.0,
            end=4.0,
            text="Cara cepat cuan!",
            words=[
                TranscriptWord(word="Cara", start=0.1, end=0.8),
                TranscriptWord(word="cepat", start=0.8, end=1.6),
                TranscriptWord(word="cuan!", start=1.6, end=3.5),
            ],
        )
    ]
    full_img = render_preview_frame(
        source_frame_bgr=bg,
        config=cfg,
        rel_time_sec=1.2,
        segments=segs,
        clip_start_sec=0.0,
        clip_end_sec=10.0,
    )
    thumb = full_img.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    _THUMB_MEMORY_CACHE[cache_key] = thumb
    return thumb
