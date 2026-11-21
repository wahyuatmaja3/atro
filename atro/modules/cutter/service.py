"""CutterService performing padded accurate-seek FFmpeg rendering with viral editing features."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Optional

from atro.config import AppSettings
from atro.core.composer_schema import ClipComposerConfig
from atro.core.logging_utils import JobLogger
from atro.core.presets import (
    apply_caption_recipe,
    build_preset_config,
    build_ready_template_config,
    extract_transcript_overrides,
    save_job_clip_config,
)
from atro.core.schemas import (
    AnalysisResult,
    DownloadResult,
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
from atro.modules.cutter.opening_narrator import synthesize_opening_voiceover
from atro.modules.cutter.subtitle_burner import generate_clip_ass_file
from atro.modules.cutter.viral_effects import build_viral_video_overlay_filters

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify_title(title: str, max_len: int = 44) -> str:
    lowered = title.lower().strip()
    slug = _SLUG_RE.sub("_", lowered).strip("_")
    if not slug:
        return "clip"
    return slug[:max_len].strip("_") or "clip"


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
        cfg.layout = tpl_cfg.layout
        cfg.subtitles = tpl_cfg.subtitles
        cfg.overlays = tpl_cfg.overlays

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
        if place in ("bottom_safe", "split_line_center", "top_safe"):
            cfg.subtitles.placement_preset = place  # type: ignore[assignment]
            cfg.sync_subtitle_anchor()

    if "subtitle_font_size" in overrides and overrides["subtitle_font_size"] is not None:
        cfg.subtitles.font_size = max(20, min(140, int(overrides["subtitle_font_size"])))

    if "subtitle_max_words" in overrides and overrides["subtitle_max_words"] is not None:
        cfg.subtitles.max_words_per_line = max(1, min(8, int(overrides["subtitle_max_words"])))

    if "hook_enabled" in overrides and overrides["hook_enabled"] is not None:
        cfg.overlays.hook_banner.enabled = bool(overrides["hook_enabled"])

    if "hook_bg_hex" in overrides and overrides["hook_bg_hex"]:
        cfg.overlays.hook_banner.bg_color_hex = str(overrides["hook_bg_hex"])

    if "zoom_punch_enabled" in overrides and overrides["zoom_punch_enabled"] is not None:
        cfg.overlays.climax_effects.zoom_punch_enabled = bool(overrides["zoom_punch_enabled"])

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

    return cfg


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
                    update_render_result=False,
                )
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

            slug = _slugify_title(clip.title)
            output_path = clips_dir / f"clip_{idx:02d}_{slug}.mp4"
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
        update_render_result: bool = True,
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

        # Auto-detect face & camera-angle crops when requested by layout config
        if (
            config.layout.framing_tracking_mode == "auto_face"
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
            )
            logger.info(
                "cutter",
                f"Deteksi wajah multi-angle untuk klip #{config.clip_index}: "
                f"{len(detected_segs)} segmen kamera terdeteksi.",
            )

        saved_cfg_path = save_job_clip_config(job_dir, config)
        segs = transcript_segments or []
        idx = config.clip_index
        slug = _slugify_title(config.clip_title)
        output_path = clips_dir / f"clip_{idx:02d}_{slug}.mp4"
        temp_filter_path = clips_dir / f"_cmp_{idx:02d}_filter.txt"
        temp_tts_base = clips_dir / f"_cmp_{idx:02d}_intro_tts"
        tts_path: Optional[Path] = None
        plan = None

        try:
            intro_dur = 0.0
            if config.timing.opening_tts_enabled:
                tts_voice = settings.tts_voice if settings is not None else "id-ID-GadisNeural"
                tts_rate = settings.tts_rate if settings is not None else "+8%"
                tts_path, intro_dur = await synthesize_opening_voiceover(
                    text=config.clip_title,
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
            bgm_input_idx = next_audio_idx + (1 if has_intro else 0)

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
            requested_start_sec=config.timing.start_time,
            requested_end_sec=config.timing.end_time,
            padded_start_sec=padded_start,
            padded_end_sec=padded_end,
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
