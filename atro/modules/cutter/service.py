"""CutterService performing padded accurate-seek FFmpeg rendering with viral editing features."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.schemas import (
    AnalysisResult,
    DownloadResult,
    JobRequest,
    PreflightReport,
    RenderResult,
    RenderedClip,
    TranscriptResult,
)
from atro.core.subprocess_utils import JobCancelledError, run_subprocess_streaming
from atro.modules.cutter.backsound import (
    build_audio_filter_chain,
    pick_backsound_for_clip,
    scan_backsound_tracks,
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
