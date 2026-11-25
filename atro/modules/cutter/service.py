"""CutterService performing padded accurate-seek FFmpeg rendering with viral editing features."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional

from atro.config import AppSettings
from atro.core.composer_schema import ClipComposerConfig, ComposerSfxCue, TranscriptSegmentOverride
from atro.core.logging_utils import JobLogger
from atro.core.presets import (
    apply_caption_recipe,
    build_preset_config,
    build_ready_template_config,
    extract_transcript_overrides,
    load_or_create_job_clip_config,
    save_job_clip_config,
)
from atro.core.schemas import (
    AnalysisResult,
    DownloadResult,
    FocusHintItem,
    JobRequest,
    PreflightReport,
    RenderResult,
    RenderedClip,
    TranscriptResult,
    TranscriptSegment,
)
from atro.core.subprocess_utils import JobCancelledError, run_subprocess_streaming
from atro.modules.cutter.backsound import (
    build_audio_filter_chain,
    pick_backsound_for_clip,
    scan_backsound_tracks,
    select_backsound_track,
)
from atro.modules.cutter.composer_renderer import (
    apply_auto_detect_to_layout,
    compile_composer_ffmpeg_plan,
)
from atro.modules.cutter.framing import FramingContext, get_framing_strategy
from atro.modules.cutter.jump_cut import (
    build_trim_ffmpeg_command,
    collect_words,
    is_filler,
    plan_keep_ranges,
    remap_segments,
    remap_time,
    remap_words,
    trimmed_duration,
)
from atro.modules.cutter.opening_narrator import synthesize_opening_voiceover
from atro.modules.cutter.subtitle_burner import generate_clip_ass_file
from atro.modules.cutter.viral_effects import build_viral_video_overlay_filters

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ILLEGAL_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _slugify_title(title: str, max_len: int = 44) -> str:
    lowered = title.lower().strip()
    slug = _SLUG_RE.sub("_", lowered).strip("_")
    if not slug:
        return "clip"
    return slug[:max_len].strip("_") or "clip"


def sanitize_clip_filename_stem(title: str, max_len: int = 90) -> str:
    """Turn clip title directly into a clean OS-safe filename stem without 'clip_01' prefix."""
    cleaned = _ILLEGAL_FILENAME_RE.sub(" ", (title or "").replace("\r", " ").replace("\n", " "))
    cleaned = " ".join(cleaned.split()).strip(" ._-")
    if not cleaned:
        return "Klip Viral"
    if len(cleaned) > max_len:
        cut = cleaned[:max_len]
        if " " in cut and cut.rfind(" ") >= max_len // 2:
            cut = cut[: cut.rfind(" ")]
        cleaned = cut.strip(" ._-") or "Klip Viral"
    if cleaned.upper() in _WINDOWS_RESERVED_STEMS:
        cleaned = f"{cleaned} (klip)"
    return cleaned


def resolve_clip_output_path(
    clips_dir: Path,
    title: str,
    clip_index: int,
    job_dir: Optional[Path] = None,
    reserved_paths: Optional[set[Path]] = None,
) -> Path:
    """Pick clean direct `<Title>.mp4` path while avoiding colliding with sibling clips."""
    stem = sanitize_clip_filename_stem(title)
    blocked: set[Path] = set()
    if reserved_paths:
        blocked.update(p.resolve() for p in reserved_paths)
    if job_dir is not None:
        rr_path = job_dir / "render_result.json"
        if rr_path.is_file():
            try:
                rr = RenderResult.model_validate_json(rr_path.read_text(encoding="utf-8"))
                for existing in rr.rendered_clips:
                    if existing.clip_index != clip_index and existing.output_path:
                        blocked.add(Path(existing.output_path).resolve())
            except Exception:
                pass

    cand = clips_dir / f"{stem}.mp4"
    if cand.resolve() not in blocked:
        return cand
    suffix = 2
    while True:
        alt = clips_dir / f"{stem} ({suffix}).mp4"
        if alt.resolve() not in blocked:
            return alt
        suffix += 1


def apply_initial_style_overrides(
    cfg: ClipComposerConfig,
    overrides: Optional[dict[str, Any]],
) -> ClipComposerConfig:
    """Apply upfront Step-2 style selections onto a ClipComposerConfig."""
    if not overrides:
        return cfg

    if overrides.get("ready_template_id"):
        tpl_cfg = build_ready_template_config(
            template_id=str(overrides["ready_template_id"]),
            clip_title=cfg.clip_title,
            start_time=cfg.timing.start_time,
            end_time=cfg.timing.end_time,
            clip_index=cfg.clip_index,
            padding_sec=cfg.timing.padding_sec,
            backsound_dir=cfg.audio.backsound_dir,
        )
        cfg.preset_id = tpl_cfg.preset_id
        cfg.preset_name = tpl_cfg.preset_name
        cfg.timing.silence_trim_enabled = tpl_cfg.timing.silence_trim_enabled
        cfg.timing.silence_max_gap_sec = tpl_cfg.timing.silence_max_gap_sec
        cfg.timing.silence_remove_fillers = tpl_cfg.timing.silence_remove_fillers
        cfg.layout = tpl_cfg.layout
        cfg.subtitles = tpl_cfg.subtitles
        cfg.overlays = tpl_cfg.overlays
        cfg.audio.contextual_sfx_enabled = tpl_cfg.audio.contextual_sfx_enabled

    if overrides.get("layout_mode"):
        lm = str(overrides["layout_mode"])
        if lm in ("single_focus", "split_screen", "blurred_frame", "multi_speaker_split", "pip_facecam"):
            cfg.layout.mode = lm  # type: ignore[assignment]

    if "pip_shape" in overrides and overrides["pip_shape"] in ("circle", "rounded_rect"):
        cfg.layout.pip_shape = str(overrides["pip_shape"])  # type: ignore[assignment]

    if "pip_position" in overrides and overrides["pip_position"] in (
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
    ):
        cfg.layout.pip_position = str(overrides["pip_position"])  # type: ignore[assignment]

    if overrides.get("pip_scale") is not None:
        cfg.layout.pip_scale = max(0.18, min(0.55, float(overrides["pip_scale"])))

    if overrides.get("frame_corner_radius_px") is not None:
        cfg.layout.frame_corner_radius_px = max(0, min(120, int(overrides["frame_corner_radius_px"])))

    if overrides.get("frame_border_width_px") is not None:
        cfg.layout.frame_border_width_px = max(0, min(24, int(overrides["frame_border_width_px"])))

    if overrides.get("frame_border_color_hex"):
        cfg.layout.frame_border_color_hex = str(overrides["frame_border_color_hex"])

    if "split_ratio_top" in overrides and overrides["split_ratio_top"] is not None:
        val = float(overrides["split_ratio_top"])
        cfg.layout.split_ratio_top = max(0.20, min(0.80, val))
        if cfg.subtitles.placement_preset == "split_line_center":
            cfg.sync_subtitle_anchor()

    if "divider_angle_deg" in overrides and overrides["divider_angle_deg"] is not None:
        cfg.layout.divider.angle_deg = max(-25.0, min(25.0, float(overrides["divider_angle_deg"])))

    if "divider_color_hex" in overrides and overrides["divider_color_hex"]:
        cfg.layout.divider.color_hex = str(overrides["divider_color_hex"])

    if overrides.get("caption_recipe"):
        apply_caption_recipe(cfg, str(overrides["caption_recipe"]))

    if "caption_bg_mode" in overrides and overrides["caption_bg_mode"] in (
        "none",
        "active_word_pill",
        "full_line_box",
    ):
        cfg.subtitles.caption_bg_mode = str(overrides["caption_bg_mode"])  # type: ignore[assignment]

    if overrides.get("active_word_bg_hex"):
        cfg.subtitles.active_word_bg_hex = str(overrides["active_word_bg_hex"])

    if "dual_keyword_enabled" in overrides and overrides["dual_keyword_enabled"] is not None:
        cfg.subtitles.dual_keyword_enabled = bool(overrides["dual_keyword_enabled"])

    if overrides.get("secondary_keyword_color_hex"):
        cfg.subtitles.secondary_keyword_color_hex = str(overrides["secondary_keyword_color_hex"])

    if "auto_emoji_enabled" in overrides and overrides["auto_emoji_enabled"] is not None:
        cfg.subtitles.auto_emoji_enabled = bool(overrides["auto_emoji_enabled"])

    if "subtitle_highlight_hex" in overrides and overrides["subtitle_highlight_hex"]:
        cfg.subtitles.highlight_color_hex = str(overrides["subtitle_highlight_hex"])

    if "subtitle_placement" in overrides and overrides["subtitle_placement"]:
        place = str(overrides["subtitle_placement"])
        if place in ("bottom_safe", "split_line_center", "top_safe", "above_speaker"):
            cfg.subtitles.placement_preset = place  # type: ignore[assignment]
            cfg.sync_subtitle_anchor()

    if "subtitle_font_size" in overrides and overrides["subtitle_font_size"] is not None:
        cfg.subtitles.font_size = max(20, min(140, int(overrides["subtitle_font_size"])))

    if "subtitle_max_words" in overrides and overrides["subtitle_max_words"] is not None:
        cfg.subtitles.max_words_per_line = max(1, min(8, int(overrides["subtitle_max_words"])))

    if "hook_enabled" in overrides and overrides["hook_enabled"] is not None:
        cfg.overlays.hook_banner.enabled = bool(overrides["hook_enabled"])

    if overrides.get("hook_design_style"):
        from atro.core.presets import HOOK_STYLE_RECIPES, apply_hook_style_recipe

        h_sid = str(overrides["hook_design_style"]).strip()
        if h_sid in HOOK_STYLE_RECIPES:
            apply_hook_style_recipe(cfg, h_sid)

    if overrides.get("opening_intro_mode") in ("freeze_clear", "freeze_zoom", "freeze_blur"):
        cfg.overlays.hook_banner.opening_intro_mode = overrides["opening_intro_mode"]

    if "opening_tts_enabled" in overrides and overrides["opening_tts_enabled"] is not None:
        cfg.timing.opening_tts_enabled = bool(overrides["opening_tts_enabled"])

    if "hook_bg_hex" in overrides and overrides["hook_bg_hex"]:
        cfg.overlays.hook_banner.bg_color_hex = str(overrides["hook_bg_hex"])

    if "zoom_punch_enabled" in overrides and overrides["zoom_punch_enabled"] is not None:
        cfg.overlays.climax_effects.zoom_punch_enabled = bool(overrides["zoom_punch_enabled"])

    if overrides.get("zoom_punch_repeat_sec") is not None:
        cfg.overlays.climax_effects.zoom_punch_repeat_sec = max(
            0.0, min(30.0, float(overrides["zoom_punch_repeat_sec"]))
        )

    if "silence_trim_enabled" in overrides and overrides["silence_trim_enabled"] is not None:
        cfg.timing.silence_trim_enabled = bool(overrides["silence_trim_enabled"])

    if overrides.get("silence_max_gap_sec") is not None:
        cfg.timing.silence_max_gap_sec = max(0.10, min(2.0, float(overrides["silence_max_gap_sec"])))

    if "contextual_sfx_enabled" in overrides and overrides["contextual_sfx_enabled"] is not None:
        cfg.audio.contextual_sfx_enabled = bool(overrides["contextual_sfx_enabled"])

    if "vignette_enabled" in overrides and overrides["vignette_enabled"] is not None:
        cfg.overlays.climax_effects.vignette_enabled = bool(overrides["vignette_enabled"])

    if "topic_header_enabled" in overrides and overrides["topic_header_enabled"] is not None:
        cfg.overlays.climax_effects.topic_header_enabled = bool(overrides["topic_header_enabled"])

    if overrides.get("topic_header_text") is not None:
        cfg.overlays.climax_effects.topic_header_text = str(overrides["topic_header_text"])

    if "framing_mode" in overrides and overrides["framing_mode"] and cfg.layout.mode == "single_focus":
        fm = str(overrides["framing_mode"])
        if fm == "fit_all_blur":
            cfg.layout.mode = "blurred_frame"
        elif fm == "center_crop":
            cfg.layout.framing_tracking_mode = "manual_crop"
            cfg.layout.top_crop.auto_detect = False
            cfg.layout.top_crop.x = 0.34
            cfg.layout.top_crop.y = 0.0
            cfg.layout.top_crop.w = 0.32
            cfg.layout.top_crop.h = 1.0

    tr = cfg.layout.transition
    if overrides.get("transition_mode") in ("auto", "fixed"):
        tr.mode = str(overrides["transition_mode"])  # type: ignore[assignment]
    if overrides.get("transition_type") in (
        "none",
        "fade",
        "crossfade",
        "dip_black",
        "slide_left",
        "slide_right",
        "slide_up",
        "slide_down",
        "zoom_in",
        "zoom_out",
        "whip",
        "blur_swap",
        "glitch",
        "wipe",
    ):
        tr.type = str(overrides["transition_type"])  # type: ignore[assignment]
    if overrides.get("transition_duration_sec") is not None:
        tr.duration_sec = max(0.10, min(0.60, float(overrides["transition_duration_sec"])))
    if overrides.get("transition_easing") in ("linear", "ease_out", "ease_in_out"):
        tr.easing = str(overrides["transition_easing"])  # type: ignore[assignment]
    if isinstance(overrides.get("transition_sfx"), str) and overrides["transition_sfx"].strip():
        tr.sfx = overrides["transition_sfx"].strip()
    if overrides.get("transition_sfx_volume") is not None:
        tr.sfx_volume = max(0.0, min(1.0, float(overrides["transition_sfx_volume"])))

    return cfg


def apply_style_template_from_config(
    style_source: ClipComposerConfig,
    target_cfg: ClipComposerConfig,
    reset_auto_face: bool = True,
) -> ClipComposerConfig:
    """Copy layout, subtitle, overlay, and audio styling from `style_source` into `target_cfg`."""
    target_cfg.preset_id = style_source.preset_id
    target_cfg.preset_name = style_source.preset_name
    target_cfg.aspect_ratio = style_source.aspect_ratio
    target_cfg.canvas_width = style_source.canvas_width
    target_cfg.canvas_height = style_source.canvas_height
    target_cfg.timing.padding_sec = style_source.timing.padding_sec
    target_cfg.timing.opening_tts_enabled = style_source.timing.opening_tts_enabled
    target_cfg.timing.silence_trim_enabled = style_source.timing.silence_trim_enabled
    target_cfg.timing.silence_max_gap_sec = style_source.timing.silence_max_gap_sec
    target_cfg.timing.silence_remove_fillers = style_source.timing.silence_remove_fillers
    target_cfg.audio.contextual_sfx_enabled = style_source.audio.contextual_sfx_enabled

    target_cfg.layout = style_source.layout.model_copy(deep=True)
    if reset_auto_face and target_cfg.layout.framing_tracking_mode == "auto_face":
        target_cfg.layout.top_crop.auto_detect = True
        target_cfg.layout.bottom_crop.auto_detect = True
        target_cfg.layout.shot_crops = []

    target_cfg.subtitles = style_source.subtitles.model_copy(deep=True)
    target_cfg.overlays.watermark = style_source.overlays.watermark.model_copy(deep=True)
    target_cfg.overlays.climax_effects = style_source.overlays.climax_effects.model_copy(deep=True)
    if target_cfg.overlays.climax_effects.topic_header_enabled:
        target_cfg.overlays.climax_effects.topic_header_text = target_cfg.clip_title[:42].upper()

    hook_copy = style_source.overlays.hook_banner.model_copy(deep=True)
    hook_copy.text = target_cfg.clip_title
    target_cfg.overlays.hook_banner = hook_copy

    if not target_cfg.overlays.comment_bubbles and style_source.overlays.comment_bubbles:
        target_cfg.overlays.comment_bubbles = [
            b.model_copy(deep=True) for b in style_source.overlays.comment_bubbles
        ]

    target_cfg.audio = style_source.audio.model_copy(deep=True)
    return target_cfg


def compute_padded_window(
    start_sec: float,
    end_sec: float,
    padding_sec: float,
    video_duration_sec: float,
) -> tuple[float, float]:
    """Expand clip start/end by padding_sec and clamp safely within [0, video_duration_sec]."""
    padded_start = max(0.0, round(start_sec - padding_sec, 3))
    padded_end = min(video_duration_sec, round(end_sec + padding_sec, 3))
    if padded_end <= padded_start:
        padded_end = min(video_duration_sec, round(padded_start + 0.5, 3))
    return padded_start, padded_end


def build_ffmpeg_cut_command(
    ffmpeg_path: Path,
    source_path: Path,
    output_path: Path,
    padded_start_sec: float,
    padded_end_sec: float,
    video_filter: str,
    has_audio: bool,
) -> list[str]:
    """Build hybrid accurate-seek FFmpeg command preventing black frames and A/V desync."""
    duration = max(0.5, round(padded_end_sec - padded_start_sec, 3))
    cmd: list[str] = [str(ffmpeg_path), "-y", "-hide_banner", "-loglevel", "warning"]

    if padded_start_sec > 5.0:
        coarse_seek = round(padded_start_sec - 3.0, 3)
        fine_seek = 3.0
        cmd.extend(
            [
                "-ss",
                f"{coarse_seek:.3f}",
                "-i",
                str(source_path),
                "-ss",
                f"{fine_seek:.3f}",
                "-t",
                f"{duration:.3f}",
            ]
        )
    else:
        cmd.extend(
            [
                "-i",
                str(source_path),
                "-ss",
                f"{padded_start_sec:.3f}",
                "-t",
                f"{duration:.3f}",
            ]
        )

    cmd.extend(
        [
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
        ]
    )

    if has_audio:
        cmd.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-af",
                "aresample=async=1:first_pts=0",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
            ]
        )
    else:
        cmd.extend(["-map", "0:v:0", "-an"])

    cmd.extend(
        [
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return cmd


def build_ffmpeg_viral_cut_command(
    ffmpeg_path: Path,
    source_path: Path,
    output_path: Path,
    padded_start_sec: float,
    padded_end_sec: float,
    filter_script_path: Path,
    intro_tts_path: Optional[Path] = None,
    backsound_path: Optional[Path] = None,
    audio_output_label: Optional[str] = "aout",
    video_output_label: str = "vout",
) -> list[str]:
    """Build frame-accurate input-seek FFmpeg command executing a filter_complex_script."""
    duration = max(0.5, round(padded_end_sec - padded_start_sec, 3))
    cmd: list[str] = [
        str(ffmpeg_path),
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-ss",
        f"{padded_start_sec:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
    ]

    if intro_tts_path is not None:
        cmd.extend(["-i", str(intro_tts_path)])

    if backsound_path is not None:
        cmd.extend(["-stream_loop", "-1", "-i", str(backsound_path)])

    cmd.extend(
        [
            "-filter_complex_script",
            str(filter_script_path),
            "-map",
            f"[{video_output_label}]",
        ]
    )

    if audio_output_label:
        cmd.extend(
            [
                "-map",
                f"[{audio_output_label}]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
            ]
        )
    else:
        cmd.append("-an")

    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return cmd


def build_ffmpeg_composer_cut_command(
    ffmpeg_path: Path,
    source_path: Path,
    output_path: Path,
    padded_start_sec: float,
    padded_end_sec: float,
    filter_script_path: Path,
    extra_video_inputs: list[tuple[Path, bool]],
    intro_tts_path: Optional[Path] = None,
    backsound_path: Optional[Path] = None,
    transition_sfx_paths: Optional[list[Path]] = None,
    audio_output_label: Optional[str] = "aout",
    video_output_label: str = "vout",
) -> list[str]:
    """Build FFmpeg command executing a ClipComposerConfig filtergraph and overlay inputs."""
    duration = max(0.5, round(padded_end_sec - padded_start_sec, 3))
    cmd: list[str] = [
        str(ffmpeg_path),
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-ss",
        f"{padded_start_sec:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
    ]

    for asset_path, loop_flag in extra_video_inputs:
        if loop_flag:
            cmd.extend(["-loop", "1", "-i", str(asset_path)])
        else:
            cmd.extend(["-i", str(asset_path)])

    if intro_tts_path is not None:
        cmd.extend(["-i", str(intro_tts_path)])

    if backsound_path is not None:
        cmd.extend(["-stream_loop", "-1", "-i", str(backsound_path)])

    for sfx_path in transition_sfx_paths or []:
        cmd.extend(["-i", str(sfx_path)])

    cmd.extend(
        [
            "-filter_complex_script",
            str(filter_script_path),
            "-map",
            f"[{video_output_label}]",
        ]
    )

    if audio_output_label:
        cmd.extend(
            [
                "-map",
                f"[{audio_output_label}]",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
            ]
        )
    else:
        cmd.append("-an")

    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return cmd


class CutterService:
    """Cuts curated clips with format-aware framing, TTS opening, viral captions, and backsound."""

    async def run(
        self,
        job_id: str,
        download: DownloadResult,
        analysis: AnalysisResult,
        request: JobRequest,
        job_dir: Path,
        preflight: PreflightReport,
        logger: JobLogger,
        on_progress: Optional[Callable[[float], Awaitable[None] | None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        transcript: Optional[TranscriptResult] = None,
        settings: Optional[AppSettings] = None,
    ) -> RenderResult:
        if cancel_event and cancel_event.is_set():
            raise JobCancelledError("Job dibatalkan sebelum rendering klip.")

        if not preflight.ffmpeg.available or not preflight.ffmpeg.path:
            raise RuntimeError("Binary ffmpeg tidak tersedia untuk proses pemotongan video.")

        ffmpeg_path = Path(preflight.ffmpeg.path)
        ffprobe_path = (
            Path(preflight.ffprobe.path)
            if (preflight.ffprobe.available and preflight.ffprobe.path)
            else None
        )
        source_path = Path(download.source_video_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"Video sumber tidak ditemukan: {source_path}")

        clips_dir = job_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        strategy = get_framing_strategy(
            request.aspect_ratio,
            request.framing_mode,
            request.edit_format,
        )
        total_clips = len(analysis.clips)
        if total_clips == 0:
            raise ValueError("Tidak ada klip dalam hasil analisis untuk dipotong.")

        tracks: list[Path] = []
        if request.enable_backsound and settings is not None:
            tracks = scan_backsound_tracks(settings.backsound_dir)
            if tracks:
                logger.info(
                    "cutter",
                    f"Backsound aktif: {len(tracks)} lagu ditemukan di {settings.backsound_dir}",
                )
            else:
                logger.info(
                    "cutter",
                    f"Folder backsound kosong ({settings.backsound_dir}); klip akan dirender tanpa musik latar.",
                )

        transcript_segments = transcript.segments if transcript is not None else []
        bgm_volume = settings.backsound_volume if settings is not None else 0.14
        enable_ducking = settings.enable_backsound_ducking if settings is not None else True
        tts_voice = settings.tts_voice if settings is not None else "id-ID-GadisNeural"
        tts_rate = settings.tts_rate if settings is not None else "+8%"

        logger.info(
            "cutter",
            f"Memulai rendering {total_clips} klip (format={request.edit_format}, rasio={request.aspect_ratio}, "
            f"strategi={strategy.name}, padding={request.padding_sec:.2f}s, sub={request.enable_subtitles}, "
            f"hook={request.enable_hook_title}, tts={request.enable_opening_tts}, "
            f"bgm={len(tracks) > 0}, polish={request.enable_viral_polish})...",
        )

        rendered_clips: list[RenderedClip] = []
        reserved_output_paths: set[Path] = set()
        for idx, clip in enumerate(analysis.clips, start=1):
            if cancel_event and cancel_event.is_set():
                raise JobCancelledError("Rendering dibatalkan oleh pengguna.")

            if request.edit_format in ("twitch_comment", "subathon") or bool(request.initial_style_overrides):
                comp_cfg = build_preset_config(
                    preset_id=request.edit_format,
                    clip_title=clip.title,
                    start_time=clip.start_time,
                    end_time=clip.end_time,
                    clip_index=idx,
                    padding_sec=request.padding_sec,
                    backsound_dir=str(settings.backsound_dir) if settings is not None else None,
                )
                comp_cfg.aspect_ratio = request.aspect_ratio
                if request.aspect_ratio == "16:9":
                    comp_cfg.canvas_width, comp_cfg.canvas_height = 1920, 1080
                    comp_cfg.sync_subtitle_anchor()
                comp_cfg.timing.opening_tts_enabled = request.enable_opening_tts
                comp_cfg.subtitles.enabled = request.enable_subtitles
                comp_cfg.overlays.hook_banner.enabled = (
                    comp_cfg.overlays.hook_banner.enabled and request.enable_hook_title
                )
                comp_cfg.audio.backsound_enabled = request.enable_backsound
                if settings is not None:
                    comp_cfg.audio.backsound_volume = settings.backsound_volume
                    comp_cfg.audio.audio_ducking = settings.enable_backsound_ducking
                comp_cfg.overlays.climax_effects.contrast_boost_enabled = request.enable_viral_polish
                comp_cfg.overlays.climax_effects.progress_bar_enabled = request.enable_viral_polish
                overrides_with_framing = dict(request.initial_style_overrides or {})
                overrides_with_framing.setdefault("framing_mode", request.framing_mode)
                apply_initial_style_overrides(comp_cfg, overrides_with_framing)
                comp_cfg.transcript_overrides = extract_transcript_overrides(
                    transcript_segments,
                    clip.start_time,
                    clip.end_time,
                    request.padding_sec,
                )
                clip_win_start = max(0.0, clip.start_time - request.padding_sec)
                comp_cfg.audio.sfx_cues = [
                    ComposerSfxCue(
                        time_sec=max(0.0, round(c.t_sec - clip_win_start, 3)),
                        sfx_name=c.sfx_name,
                        volume=c.volume,
                        reason=c.reason,
                    )
                    for c in (getattr(clip, "sfx_cues", None) or [])
                    if c.t_sec >= clip_win_start
                ]
                rendered_item = await self.render_clip_with_composer_config(
                    job_id=job_id,
                    download=download,
                    config=comp_cfg,
                    job_dir=job_dir,
                    preflight=preflight,
                    logger=logger,
                    transcript_segments=transcript_segments,
                    settings=settings,
                    cancel_event=cancel_event,
                    virality_score=clip.virality_score,
                    reason=clip.reason,
                    focus_hints=clip.focus_hints,
                    update_render_result=False,
                    reserved_output_paths=reserved_output_paths,
                )
                reserved_output_paths.add(Path(rendered_item.output_path).resolve())
                rendered_clips.append(rendered_item)
                if on_progress:
                    pct = (idx / total_clips) * 100.0
                    res = on_progress(pct)
                    if asyncio.iscoroutine(res):
                        await res
                continue

            padded_start, padded_end = compute_padded_window(
                clip.start_time,
                clip.end_time,
                request.padding_sec,
                download.duration_sec,
            )
            main_dur = max(0.5, round(padded_end - padded_start, 3))

            ctx = FramingContext(
                source_path=source_path,
                source_width=download.width,
                source_height=download.height,
                fps=download.fps,
                start_sec=padded_start,
                end_sec=padded_end,
                aspect_ratio=request.aspect_ratio,
            )
            framing_plan = await strategy.build_framing_plan(ctx)

            output_path = resolve_clip_output_path(
                clips_dir=clips_dir,
                title=clip.title,
                clip_index=idx,
                reserved_paths=reserved_output_paths,
            )
            reserved_output_paths.add(output_path.resolve())
            temp_ass_path = clips_dir / f"_clip_{idx:02d}_subs.ass"
            temp_filter_path = clips_dir / f"_clip_{idx:02d}_filter.txt"
            temp_tts_base = clips_dir / f"_clip_{idx:02d}_intro_tts"
            tts_path: Optional[Path] = None

            try:
                intro_dur = 0.0
                if request.enable_opening_tts:
                    tts_path, intro_dur = await synthesize_opening_voiceover(
                        text=clip.title,
                        output_audio_path=temp_tts_base,
                        voice=tts_voice,
                        rate=tts_rate,
                        ffprobe_path=ffprobe_path,
                        logger=logger,
                    )

                has_intro = tts_path is not None and intro_dur > 0.0
                total_dur = round(main_dur + (intro_dur if has_intro else 0.0), 3)

                video_filter_lines = list(framing_plan.filter_lines)
                overlay_in_label = framing_plan.output_label

                if has_intro:
                    fps_safe = max(10.0, min(120.0, float(download.fps or 30.0)))
                    video_filter_lines.extend(
                        [
                            f"[{framing_plan.output_label}]fps={fps_safe:.3f},split=2[fr_intro_src][fr_main]",
                            (
                                f"[fr_intro_src]trim=start_frame=0:end_frame=1,"
                                f"tpad=stop_mode=clone:stop_duration={intro_dur:.3f},"
                                f"trim=0:{intro_dur:.3f},setpts=PTS-STARTPTS,"
                                f"boxblur=10:2,eq=brightness=-0.16,setsar=1[v_intro]"
                            ),
                            "[fr_main]setpts=PTS-STARTPTS[v_main_r]",
                            "[v_intro][v_main_r]concat=n=2:v=1:a=0,setsar=1[v_with_intro]",
                        ]
                    )
                    overlay_in_label = "v_with_intro"

                use_subs = bool(request.enable_subtitles and transcript_segments)
                ass_path = generate_clip_ass_file(
                    output_ass_path=temp_ass_path,
                    aspect_ratio=request.aspect_ratio,
                    clip_duration_sec=total_dur,
                    clip_title=clip.title,
                    segments=transcript_segments,
                    clip_start_sec=padded_start,
                    clip_end_sec=padded_end,
                    enable_subtitles=use_subs,
                    enable_hook_title=request.enable_hook_title,
                    edit_format=request.edit_format,
                    opening_duration_sec=intro_dur if has_intro else 0.0,
                    source_credit=download.channel,
                )

                bgm_track = (
                    pick_backsound_for_clip(tracks, idx)
                    if (request.enable_backsound and tracks)
                    else None
                )

                overlay_lines = build_viral_video_overlay_filters(
                    in_label=overlay_in_label,
                    out_label="vout",
                    width=framing_plan.output_width,
                    height=framing_plan.output_height,
                    duration_sec=total_dur,
                    ass_filename=ass_path.name if ass_path is not None else None,
                    enable_viral_polish=request.enable_viral_polish,
                    edit_format=request.edit_format,
                )

                intro_input_idx = 1 if has_intro else None
                bgm_input_idx = 2 if has_intro else 1
                audio_lines, audio_label = build_audio_filter_chain(
                    has_source_audio=download.has_audio,
                    has_backsound=(bgm_track is not None),
                    duration_sec=main_dur,
                    bgm_volume=bgm_volume,
                    enable_ducking=enable_ducking,
                    enable_voice_polish=request.enable_viral_polish,
                    intro_tts_input_idx=intro_input_idx,
                    intro_duration_sec=intro_dur if has_intro else 0.0,
                    bgm_input_idx=bgm_input_idx,
                )

                filter_script_text = ";\n".join(video_filter_lines + overlay_lines + audio_lines)
                temp_filter_path.write_text(filter_script_text, encoding="utf-8")

                cmd = build_ffmpeg_viral_cut_command(
                    ffmpeg_path=ffmpeg_path,
                    source_path=source_path,
                    output_path=output_path,
                    padded_start_sec=padded_start,
                    padded_end_sec=padded_end,
                    filter_script_path=temp_filter_path,
                    intro_tts_path=tts_path if has_intro else None,
                    backsound_path=bgm_track,
                    audio_output_label=audio_label,
                    video_output_label="vout",
                )

                logger.info(
                    "cutter",
                    f"[{idx}/{total_clips}] Merender '{clip.title}' "
                    f"(format={request.edit_format}, {padded_start:.2f}s->{padded_end:.2f}s, "
                    f"intro={intro_dur:.2f}s, framing={framing_plan.summary}, "
                    f"bgm={bgm_track.name if bgm_track else '-'}) ke {output_path.name}...",
                )

                rc, lines = await run_subprocess_streaming(
                    cmd,
                    cancel_event=cancel_event,
                    cwd=clips_dir,
                )
                if rc != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
                    raise RuntimeError(
                        f"FFmpeg gagal merender {output_path.name} (exit {rc}): {' | '.join(lines[-5:])}"
                    )

                subtitles_burned = bool(ass_path is not None and use_subs)
                backsound_used = bgm_track.name if bgm_track is not None else None
            finally:
                temp_ass_path.unlink(missing_ok=True)
                temp_filter_path.unlink(missing_ok=True)
                temp_tts_base.with_suffix(".mp3").unlink(missing_ok=True)
                temp_tts_base.with_suffix(".wav").unlink(missing_ok=True)
                if tts_path is not None:
                    tts_path.unlink(missing_ok=True)

            file_size = output_path.stat().st_size
            default_cfg = build_preset_config(
                preset_id=request.edit_format,
                clip_title=clip.title,
                start_time=clip.start_time,
                end_time=clip.end_time,
                clip_index=idx,
                padding_sec=request.padding_sec,
                backsound_dir=str(settings.backsound_dir) if settings is not None else None,
            )
            default_cfg.aspect_ratio = request.aspect_ratio
            if request.aspect_ratio == "16:9":
                default_cfg.canvas_width, default_cfg.canvas_height = 1920, 1080
                default_cfg.sync_subtitle_anchor()
            default_cfg.timing.opening_tts_enabled = request.enable_opening_tts
            default_cfg.subtitles.enabled = request.enable_subtitles
            default_cfg.overlays.hook_banner.enabled = request.enable_hook_title
            default_cfg.audio.backsound_enabled = request.enable_backsound
            default_cfg.transcript_overrides = extract_transcript_overrides(
                transcript_segments,
                clip.start_time,
                clip.end_time,
                request.padding_sec,
            )
            if (
                request.aspect_ratio == "9:16"
                and request.framing_mode == "smart_face"
                and default_cfg.layout.framing_tracking_mode == "auto_face"
            ):
                await apply_auto_detect_to_layout(
                    source_path=source_path,
                    start_sec=padded_start,
                    end_sec=padded_end,
                    layout=default_cfg.layout,
                    src_w=download.width,
                    src_h=download.height,
                    fps=download.fps,
                    job_dir=job_dir,
                    focus_hints=clip.focus_hints,
                    transcript_segments=transcript_segments,
                )
            saved_cfg_path = save_job_clip_config(job_dir, default_cfg)

            rendered_clips.append(
                RenderedClip(
                    clip_index=idx,
                    title=clip.title,
                    virality_score=clip.virality_score,
                    reason=clip.reason,
                    requested_start_sec=clip.start_time,
                    requested_end_sec=clip.end_time,
                    padded_start_sec=padded_start,
                    padded_end_sec=padded_end,
                    output_duration_sec=total_dur,
                    output_path=str(output_path.resolve()),
                    file_size_bytes=file_size,
                    subtitles_burned=subtitles_burned,
                    backsound_used=backsound_used,
                    framing_summary=framing_plan.summary,
                    edit_format=request.edit_format,
                    opening_tts_used=has_intro,
                    intro_duration_sec=intro_dur if has_intro else 0.0,
                    composer_config_path=str(saved_cfg_path.resolve()),
                )
            )
            logger.info(
                "cutter",
                f"[{idx}/{total_clips}] Selesai: {output_path.name} ({file_size / (1024 * 1024):.2f} MB, {total_dur:.2f}s).",
            )

            if on_progress:
                pct = (idx / total_clips) * 100.0
                res = on_progress(pct)
                if asyncio.iscoroutine(res):
                    await res

        result = RenderResult(
            job_id=job_id,
            aspect_ratio=request.aspect_ratio,
            framing_strategy=strategy.name,
            padding_sec=request.padding_sec,
            output_dir=str(clips_dir.resolve()),
            rendered_clips=rendered_clips,
        )
        (job_dir / "render_result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result

    async def _build_jump_cut_source(
        self,
        download: DownloadResult,
        config: ClipComposerConfig,
        transcript_segments: Optional[list[TranscriptSegment]],
        padded_start: float,
        padded_end: float,
        clips_dir: Path,
        ffmpeg_path: Path,
        logger: JobLogger,
        cancel_event: Optional[asyncio.Event],
    ) -> Optional[tuple[DownloadResult, ClipComposerConfig, list[TranscriptSegment], Path]]:
        """Create a pause/filler-free intermediate clip. Returns `None` (render as-is) when not applicable."""
        idx = config.clip_index
        words = collect_words(transcript_segments or [], padded_start, padded_end)
        if not words:
            logger.info("cutter", f"[#{idx:02d}] Jump-cut dilewati: transkrip tidak punya timestamp per kata.")
            return None
        remove_fillers = bool(config.timing.silence_remove_fillers)
        ranges = plan_keep_ranges(
            words,
            padded_start,
            padded_end,
            max_gap_sec=float(config.timing.silence_max_gap_sec),
            remove_fillers=remove_fillers,
        )
        if not ranges:
            logger.info("cutter", f"[#{idx:02d}] Jump-cut dilewati: tidak ada jeda/filler yang layak dibuang.")
            return None

        new_dur = trimmed_duration(ranges)
        trim_path = clips_dir / f"_cmp_{idx:02d}_jumpcut.mp4"
        script_path = clips_dir / f"_cmp_{idx:02d}_jumpcut_filter.txt"
        cmd, script = build_trim_ffmpeg_command(
            ffmpeg_path=ffmpeg_path,
            source_path=Path(download.source_video_path),
            output_path=trim_path,
            window_start=padded_start,
            window_end=padded_end,
            ranges=ranges,
            filter_script_path=script_path,
            has_audio=download.has_audio,
            fps=download.fps,
        )
        script_path.write_text(script, encoding="utf-8")
        try:
            rc, lines = await run_subprocess_streaming(cmd, cancel_event=cancel_event, cwd=clips_dir)
        finally:
            script_path.unlink(missing_ok=True)
        if rc != 0 or not trim_path.is_file() or trim_path.stat().st_size == 0:
            trim_path.unlink(missing_ok=True)
            logger.info("cutter", f"[#{idx:02d}] Jump-cut gagal, render memakai klip asli: {' | '.join(lines[-3:])}")
            return None

        logger.info(
            "cutter",
            f"[#{idx:02d}] Jump-cut: {padded_end - padded_start:.1f}s -> {new_dur:.1f}s ({len(ranges)} potongan).",
        )

        new_cfg = config.model_copy(deep=True)
        new_cfg.timing.start_time = 0.0
        new_cfg.timing.end_time = max(1.0, new_dur)
        new_cfg.timing.padding_sec = 0.0
        new_cfg.timing.silence_trim_enabled = False
        new_cfg.layout.shot_crops = []  # camera timeline no longer matches; auto-face re-detects on trimmed clip
        new_cfg.layout.speaker_turns = []

        remapped_cues: list[ComposerSfxCue] = []
        for cue in config.audio.sfx_cues:
            mapped_t = remap_time(padded_start + float(cue.time_sec), ranges)
            if mapped_t is None:
                continue
            remapped_cues.append(
                ComposerSfxCue(
                    time_sec=mapped_t,
                    sfx_name=cue.sfx_name,
                    volume=cue.volume,
                    reason=cue.reason,
                )
            )
        new_cfg.audio.sfx_cues = remapped_cues

        new_segments = remap_segments(transcript_segments or [], ranges, remove_fillers)
        new_overrides: list[TranscriptSegmentOverride] = []
        for ov in config.transcript_overrides:
            span_words = remap_words(
                [w for w in ov.words if not (remove_fillers and is_filler(w.word))], ranges
            )
            if not span_words:
                continue
            joined = " ".join(w.word.strip() for w in ov.words).strip()
            text = " ".join(w.word.strip() for w in span_words).strip() if ov.text.strip() == joined else ov.text
            new_overrides.append(
                TranscriptSegmentOverride(
                    index=len(new_overrides),
                    start=span_words[0].start,
                    end=max(span_words[-1].end, span_words[0].start + 0.05),
                    text=text,
                    speaker=ov.speaker,
                    words=span_words,
                )
            )
        new_cfg.transcript_overrides = new_overrides

        new_download = download.model_copy(
            update={"source_video_path": str(trim_path), "duration_sec": max(0.5, new_dur)}
        )
        return new_download, new_cfg, new_segments, trim_path

    async def render_clip_with_composer_config(
        self,
        job_id: str,
        download: DownloadResult,
        config: ClipComposerConfig,
        job_dir: Path,
        preflight: PreflightReport,
        logger: JobLogger,
        transcript_segments: Optional[list[TranscriptSegment]] = None,
        settings: Optional[AppSettings] = None,
        cancel_event: Optional[asyncio.Event] = None,
        virality_score: int = 8,
        reason: str = "Custom Composer Render",
        focus_hints: Optional[list[FocusHintItem]] = None,
        update_render_result: bool = True,
        reserved_output_paths: Optional[set[Path]] = None,
    ) -> RenderedClip:
        """Render a single clip using full 1:1 WYSIWYG ClipComposerConfig."""
        if cancel_event and cancel_event.is_set():
            raise JobCancelledError("Rendering dibatalkan oleh pengguna.")
        if not preflight.ffmpeg.available or not preflight.ffmpeg.path:
            raise RuntimeError("Binary ffmpeg tidak tersedia untuk proses render composer.")

        ffmpeg_path = Path(preflight.ffmpeg.path)
        ffprobe_path = (
            Path(preflight.ffprobe.path)
            if (preflight.ffprobe.available and preflight.ffprobe.path)
            else None
        )
        source_path = Path(download.source_video_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"Video sumber tidak ditemukan: {source_path}")

        clips_dir = job_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)

        padded_start, padded_end = compute_padded_window(
            config.timing.start_time,
            config.timing.end_time,
            config.timing.padding_sec,
            download.duration_sec,
        )
        main_dur = max(0.5, round(padded_end - padded_start, 3))

        # Jump-cut: render from a trimmed intermediate (pauses/fillers removed), timeline restarts at 0.
        orig_config = config
        orig_padded = (padded_start, padded_end)
        trim_tmp: Optional[Path] = None
        if config.timing.silence_trim_enabled:
            trimmed = await self._build_jump_cut_source(
                download=download,
                config=config,
                transcript_segments=transcript_segments,
                padded_start=padded_start,
                padded_end=padded_end,
                clips_dir=clips_dir,
                ffmpeg_path=ffmpeg_path,
                logger=logger,
                cancel_event=cancel_event,
            )
            if trimmed is not None:
                download, config, transcript_segments, trim_tmp = trimmed
                source_path = trim_tmp
                focus_hints = None
                padded_start, padded_end = 0.0, config.timing.end_time
                main_dur = max(0.5, round(padded_end - padded_start, 3))

        # Auto-detect face & camera-angle crops when requested and not yet populated
        if (
            config.layout.framing_tracking_mode == "auto_face"
            and not config.layout.shot_crops
            and (config.layout.top_crop.auto_detect or config.layout.bottom_crop.auto_detect)
        ):
            detected_segs = await apply_auto_detect_to_layout(
                source_path=source_path,
                start_sec=padded_start,
                end_sec=padded_end,
                layout=config.layout,
                src_w=download.width,
                src_h=download.height,
                fps=download.fps,
                job_dir=None if trim_tmp is not None else job_dir,
                focus_hints=focus_hints,
                transcript_segments=transcript_segments,
            )
            logger.info(
                "cutter",
                f"Deteksi wajah multi-angle untuk klip #{config.clip_index}: "
                f"{len(detected_segs)} segmen kamera terdeteksi.",
            )

        saved_cfg_path = save_job_clip_config(job_dir, orig_config)
        segs = transcript_segments or []
        idx = config.clip_index
        output_path = resolve_clip_output_path(
            clips_dir=clips_dir,
            title=config.clip_title,
            clip_index=idx,
            job_dir=None if reserved_output_paths is not None else job_dir,
            reserved_paths=reserved_output_paths,
        )
        temp_filter_path = clips_dir / f"_cmp_{idx:02d}_filter.txt"
        temp_tts_base = clips_dir / f"_cmp_{idx:02d}_intro_tts"
        tts_path: Optional[Path] = None
        plan = None

        try:
            intro_dur = 0.0
            if config.timing.opening_tts_enabled:
                tts_voice = settings.tts_voice if settings is not None else "id-ID-GadisNeural"
                tts_rate = settings.tts_rate if settings is not None else "+8%"
                tts_source_text = (
                    (config.overlays.hook_banner.text or "").strip()
                    if config.overlays.hook_banner.enabled and (config.overlays.hook_banner.text or "").strip()
                    else config.clip_title
                )
                tts_path, intro_dur = await synthesize_opening_voiceover(
                    text=tts_source_text,
                    output_audio_path=temp_tts_base,
                    voice=tts_voice,
                    rate=tts_rate,
                    ffprobe_path=ffprobe_path,
                    logger=logger,
                )

            has_intro = tts_path is not None and intro_dur > 0.0
            total_dur = round(main_dur + (intro_dur if has_intro else 0.0), 3)

            plan = compile_composer_ffmpeg_plan(
                config=config,
                src_w=download.width,
                src_h=download.height,
                fps=download.fps,
                duration_sec=main_dur,
                work_dir=clips_dir,
                segments=segs,
                opening_duration_sec=intro_dur if has_intro else 0.0,
                prefix=f"_cmp_{idx:02d}",
                base_extra_input_idx=1,
                clip_start_sec=padded_start,
                clip_end_sec=padded_end,
            )

            bgm_dir_str = config.audio.backsound_dir
            bgm_dir = (
                Path(bgm_dir_str)
                if bgm_dir_str
                else (settings.backsound_dir if settings is not None else None)
            )
            tracks = scan_backsound_tracks(bgm_dir) if config.audio.backsound_enabled else []
            bgm_track = (
                select_backsound_track(
                    tracks=tracks,
                    mode=config.audio.selection_mode,
                    clip_index=idx,
                    selected_filename=config.audio.selected_track_filename,
                )
                if (config.audio.backsound_enabled and tracks)
                else None
            )

            next_audio_idx = 1 + len(plan.extra_video_inputs)
            intro_input_idx = next_audio_idx if has_intro else None
            if has_intro:
                next_audio_idx += 1
            bgm_input_idx = next_audio_idx if bgm_track is not None else 1
            if bgm_track is not None:
                next_audio_idx += 1

            sfx_paths: list[Path] = []
            sfx_idx_by_path: dict[Path, int] = {}
            sfx_chain_events: list[tuple[float, int, float]] = []
            for ev_t, ev_path, ev_vol in plan.sfx_events:
                if not ev_path.is_file():
                    continue
                if ev_path not in sfx_idx_by_path:
                    sfx_idx_by_path[ev_path] = next_audio_idx
                    sfx_paths.append(ev_path)
                    next_audio_idx += 1
                sfx_chain_events.append((ev_t, sfx_idx_by_path[ev_path], ev_vol))

            audio_lines, audio_label = build_audio_filter_chain(
                has_source_audio=download.has_audio,
                has_backsound=(bgm_track is not None),
                duration_sec=main_dur,
                bgm_volume=config.audio.backsound_volume,
                enable_ducking=config.audio.audio_ducking,
                enable_voice_polish=True,
                intro_tts_input_idx=intro_input_idx,
                intro_duration_sec=intro_dur if has_intro else 0.0,
                bgm_input_idx=bgm_input_idx,
                enable_loudnorm=config.audio.vocal_loudnorm,
                transition_sfx_events=sfx_chain_events,
            )

            filter_script_text = ";\n".join(plan.filter_lines + audio_lines)
            temp_filter_path.write_text(filter_script_text, encoding="utf-8")

            cmd = build_ffmpeg_composer_cut_command(
                ffmpeg_path=ffmpeg_path,
                source_path=source_path,
                output_path=output_path,
                padded_start_sec=padded_start,
                padded_end_sec=padded_end,
                filter_script_path=temp_filter_path,
                extra_video_inputs=plan.extra_video_inputs,
                intro_tts_path=tts_path if has_intro else None,
                backsound_path=bgm_track,
                transition_sfx_paths=sfx_paths,
                audio_output_label=audio_label,
                video_output_label=plan.video_output_label,
            )

            logger.info(
                "composer",
                f"[#{idx:02d}] Export Studio '{config.clip_title}' ({plan.summary}, "
                f"{padded_start:.2f}s->{padded_end:.2f}s) ke {output_path.name}...",
            )

            rc, lines = await run_subprocess_streaming(
                cmd,
                cancel_event=cancel_event,
                cwd=clips_dir,
            )
            if rc != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
                raise RuntimeError(
                    f"FFmpeg Composer gagal merender {output_path.name} (exit {rc}): {' | '.join(lines[-6:])}"
                )

            subtitles_burned = bool(plan.ass_path is not None and config.subtitles.enabled)
            backsound_used = bgm_track.name if bgm_track is not None else None
        finally:
            temp_filter_path.unlink(missing_ok=True)
            if trim_tmp is not None:
                trim_tmp.unlink(missing_ok=True)
            temp_tts_base.with_suffix(".mp3").unlink(missing_ok=True)
            temp_tts_base.with_suffix(".wav").unlink(missing_ok=True)
            if tts_path is not None:
                tts_path.unlink(missing_ok=True)
            if plan is not None:
                for p in plan.temp_asset_paths:
                    p.unlink(missing_ok=True)
                if plan.temp_fonts_dir is not None and plan.temp_fonts_dir.exists():
                    shutil.rmtree(plan.temp_fonts_dir, ignore_errors=True)

        file_size = output_path.stat().st_size
        rendered = RenderedClip(
            clip_index=idx,
            title=config.clip_title,
            virality_score=virality_score,
            reason=reason,
            requested_start_sec=orig_config.timing.start_time,
            requested_end_sec=orig_config.timing.end_time,
            padded_start_sec=orig_padded[0],
            padded_end_sec=orig_padded[1],
            output_duration_sec=total_dur,
            output_path=str(output_path.resolve()),
            file_size_bytes=file_size,
            subtitles_burned=subtitles_burned,
            backsound_used=backsound_used,
            framing_summary=plan.summary,
            edit_format=config.preset_id,
            opening_tts_used=has_intro,
            intro_duration_sec=intro_dur if has_intro else 0.0,
            composer_config_path=str(saved_cfg_path.resolve()),
        )

        if update_render_result:
            rr_path = job_dir / "render_result.json"
            if rr_path.is_file():
                try:
                    rr = RenderResult.model_validate_json(rr_path.read_text(encoding="utf-8"))
                    replaced = False
                    for i, existing in enumerate(rr.rendered_clips):
                        if existing.clip_index == idx:
                            rr.rendered_clips[i] = rendered
                            replaced = True
                            break
                    if not replaced:
                        rr.rendered_clips.append(rendered)
                        rr.rendered_clips.sort(key=lambda c: c.clip_index)
                    rr_path.write_text(rr.model_dump_json(indent=2), encoding="utf-8")
                except Exception:
                    pass
            else:
                rr = RenderResult(
                    job_id=job_id,
                    aspect_ratio=config.aspect_ratio,
                    framing_strategy=plan.summary,
                    padding_sec=min(0.5, max(0.3, config.timing.padding_sec)),
                    output_dir=str(clips_dir.resolve()),
                    rendered_clips=[rendered],
                )
                rr_path.write_text(rr.model_dump_json(indent=2), encoding="utf-8")

        logger.info(
            "composer",
            f"[#{idx:02d}] Export Studio selesai: {output_path.name} ({file_size / (1024 * 1024):.2f} MB).",
        )
        return rendered

    async def render_batch_clips_with_composer(
        self,
        job_id: str,
        download: DownloadResult,
        analysis: AnalysisResult,
        job_dir: Path,
        preflight: PreflightReport,
        logger: JobLogger,
        clip_indices: list[int],
        transcript_segments: Optional[list[TranscriptSegment]] = None,
        settings: Optional[AppSettings] = None,
        style_template_config: Optional[ClipComposerConfig] = None,
        force_redetect_faces: bool = True,
        on_clip_progress: Optional[Callable[[int, int, str], Awaitable[None] | None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
        request: Optional[JobRequest] = None,
    ) -> list[RenderedClip]:
        """Batch-apply style & re-render multiple/all existing clips without re-transcribing."""
        valid_indices = [
            i for i in dict.fromkeys(clip_indices) if 1 <= int(i) <= len(analysis.clips)
        ]
        if not valid_indices:
            return []

        segs = transcript_segments or []
        edit_fmt = request.edit_format if request is not None else "podcast"
        pad_sec = request.padding_sec if request is not None else 0.4
        bgm_dir = str(settings.backsound_dir) if settings is not None else None
        rendered_list: list[RenderedClip] = []
        total = len(valid_indices)

        logger.info(
            "composer",
            f"[Batch] Memulai render ulang {total} klip terpilih (force_redetect_faces={force_redetect_faces})...",
        )

        for done_idx, clip_idx in enumerate(valid_indices, start=1):
            if cancel_event and cancel_event.is_set():
                raise JobCancelledError("Render batch dibatalkan oleh pengguna.")

            clip = analysis.clips[clip_idx - 1]
            cfg_path = job_dir / "clip_configs" / f"clip_{clip_idx:02d}.json"
            existed = cfg_path.is_file()
            cfg = load_or_create_job_clip_config(
                job_dir=job_dir,
                clip_index=clip_idx,
                clip_title=clip.title,
                start_time=clip.start_time,
                end_time=clip.end_time,
                edit_format=edit_fmt,
                padding_sec=pad_sec,
                transcript_segments=segs,
                backsound_dir=bgm_dir,
            )
            if not existed and request is not None and request.initial_style_overrides:
                apply_initial_style_overrides(cfg, request.initial_style_overrides)

            if style_template_config is not None:
                apply_style_template_from_config(
                    style_template_config,
                    cfg,
                    reset_auto_face=force_redetect_faces,
                )
            elif force_redetect_faces and cfg.layout.framing_tracking_mode == "auto_face":
                cfg.layout.top_crop.auto_detect = True
                cfg.layout.bottom_crop.auto_detect = True
                cfg.layout.shot_crops = []

            rendered = await self.render_clip_with_composer_config(
                job_id=job_id,
                download=download,
                config=cfg,
                job_dir=job_dir,
                preflight=preflight,
                logger=logger,
                transcript_segments=segs,
                settings=settings,
                cancel_event=cancel_event,
                virality_score=clip.virality_score,
                reason=clip.reason,
                focus_hints=clip.focus_hints,
                update_render_result=True,
            )
            rendered_list.append(rendered)
            if on_clip_progress:
                res = on_clip_progress(done_idx, total, clip.title)
                if asyncio.iscoroutine(res):
                    await res

        logger.info("composer", f"[Batch] Selesai merender ulang {len(rendered_list)} klip.")
        return rendered_list
