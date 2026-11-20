"""Unit and real-FFmpeg integration tests for ATRO viral editing features."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies
from atro.core.schemas import (
    AnalysisResult,
    ClipCandidate,
    DownloadResult,
    JobRequest,
    TranscriptResult,
    TranscriptSegment,
)
from atro.modules.cutter.backsound import (
    build_audio_filter_chain,
    pick_backsound_for_clip,
    scan_backsound_tracks,
)
from atro.modules.cutter.framing import FitAllBlur9x16Strategy, FramingContext, get_framing_strategy
from atro.modules.cutter.framing.face_tracking import (
    FaceBox,
    ShotSegmentPlan,
    _analyze_shots_sync,
    _assemble_framing_plan_from_shots,
    classify_shot_layout,
)
from atro.modules.cutter.service import CutterService, build_ffmpeg_viral_cut_command
from atro.modules.cutter.subtitle_burner import build_viral_caption_chunks, generate_clip_ass_file


def test_subtitle_chunking_and_ass_generation(tmp_path: Path) -> None:
    segments = [
        TranscriptSegment(
            index=0,
            start=10.0,
            end=14.0,
            text="Ini adalah rahasia besar untuk bikin klip viral sekarang.",
        ),
        TranscriptSegment(
            index=1,
            start=14.0,
            end=17.5,
            text="Jangan lewatkan bagian penting di akhir video ini.",
        ),
    ]
    chunks = build_viral_caption_chunks(segments, clip_start=9.6, clip_end=17.9, max_words_per_chunk=4)
    assert len(chunks) >= 3
    assert all(1 <= len(c.words) <= 4 for c in chunks)
    assert chunks[0].start == pytest.approx(0.4, abs=0.05)

    ass_path = tmp_path / "test.ass"
    out = generate_clip_ass_file(
        output_ass_path=ass_path,
        aspect_ratio="9:16",
        clip_duration_sec=8.3,
        clip_title="Rahasia Klip Viral",
        segments=segments,
        clip_start_sec=9.6,
        clip_end_sec=17.9,
        enable_subtitles=True,
        enable_hook_title=True,
    )
    assert out is not None and out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "[Script Info]" in text
    assert "Style: ViralSub" in text
    assert "Style: HookTitle" in text
    assert "&H0015CCFA&" in text
    assert "Rahasia Klip Viral" in text


def test_backsound_scan_rotation_and_audio_filters(tmp_path: Path) -> None:
    bgm_dir = tmp_path / "backsound"
    bgm_dir.mkdir()
    (bgm_dir / "b_track.mp3").write_bytes(b"mp3")
    (bgm_dir / "a_track.wav").write_bytes(b"wav")
    (bgm_dir / "readme.txt").write_text("ignore", encoding="utf-8")

    tracks = scan_backsound_tracks(bgm_dir)
    assert [t.name for t in tracks] == ["a_track.wav", "b_track.mp3"]
    assert pick_backsound_for_clip(tracks, 1).name == "a_track.wav"  # type: ignore[union-attr]
    assert pick_backsound_for_clip(tracks, 2).name == "b_track.mp3"  # type: ignore[union-attr]
    assert pick_backsound_for_clip(tracks, 3).name == "a_track.wav"  # type: ignore[union-attr]
    assert pick_backsound_for_clip([], 1) is None

    # With ducking
    lines_duck, lbl_duck = build_audio_filter_chain(
        has_source_audio=True,
        has_backsound=True,
        duration_sec=12.0,
        bgm_volume=0.14,
        enable_ducking=True,
        enable_voice_polish=True,
    )
    joined_duck = "\n".join(lines_duck)
    assert lbl_duck == "aout"
    assert "sidechaincompress" in joined_duck
    assert "normalize=0" in joined_duck
    assert "highpass=f=80" in joined_duck

    # Without ducking
    lines_mix, _ = build_audio_filter_chain(
        has_source_audio=True,
        has_backsound=True,
        duration_sec=12.0,
        bgm_volume=0.20,
        enable_ducking=False,
        enable_voice_polish=False,
    )
    joined_mix = "\n".join(lines_mix)
    assert "sidechaincompress" not in joined_mix
    assert "amix=inputs=2" in joined_mix

    # Voice only
    lines_voice, lbl_v = build_audio_filter_chain(
        has_source_audio=True,
        has_backsound=False,
        duration_sec=12.0,
        bgm_volume=0.14,
        enable_ducking=True,
        enable_voice_polish=True,
    )
    assert lbl_v == "aout"
    assert len(lines_voice) == 1


def test_smart_face_shot_layout_classification() -> None:
    # 0 faces -> center_crop
    mode0, _, _ = classify_shot_layout([[], []], 1920, 1080)
    assert mode0 == "center_crop"

    # 1 face on left side -> single_focus centered on face
    obs1 = [
        [FaceBox(x=0.15, y=0.20, w=0.12, h=0.22)],
        [FaceBox(x=0.16, y=0.20, w=0.12, h=0.22)],
    ]
    mode1, centers1, _ = classify_shot_layout(obs1, 1920, 1080)
    assert mode1 == "single_focus"
    assert abs(centers1[0][0] - 0.215) < 0.03

    # 2 faces separated left & right -> split_screen
    obs2 = [
        [FaceBox(x=0.12, y=0.20, w=0.12, h=0.22), FaceBox(x=0.72, y=0.20, w=0.12, h=0.22)],
        [FaceBox(x=0.13, y=0.21, w=0.12, h=0.22), FaceBox(x=0.73, y=0.21, w=0.12, h=0.22)],
    ]
    mode2, centers2, _ = classify_shot_layout(obs2, 1920, 1080)
    assert mode2 == "split_screen"
    assert len(centers2) == 2

    # 3+ separated faces -> fit_all_blur so everyone stays visible
    obs3 = [
        [
            FaceBox(x=0.08, y=0.22, w=0.10, h=0.18),
            FaceBox(x=0.45, y=0.22, w=0.10, h=0.18),
            FaceBox(x=0.80, y=0.22, w=0.10, h=0.18),
        ],
        [
            FaceBox(x=0.08, y=0.22, w=0.10, h=0.18),
            FaceBox(x=0.45, y=0.22, w=0.10, h=0.18),
            FaceBox(x=0.80, y=0.22, w=0.10, h=0.18),
        ],
    ]
    mode3, centers3, _ = classify_shot_layout(obs3, 1920, 1080)
    assert mode3 == "fit_all_blur"
    assert len(centers3) == 3


@pytest.mark.asyncio
async def test_viral_editor_full_integration_real_ffmpeg(tmp_path: Path) -> None:
    bgm_dir = tmp_path / "backsound"
    bgm_dir.mkdir(parents=True)
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=bgm_dir,
        backsound_volume=0.16,
        enable_backsound_ducking=True,
    )
    preflight = check_startup_dependencies(settings)
    assert preflight.ok and preflight.ffmpeg.path and preflight.ffprobe.path

    job_dir = settings.workspace_dir / "job_viral"
    job_dir.mkdir(parents=True)
    source_mp4 = job_dir / "source.mp4"
    bgm_wav = bgm_dir / "chill_loop.wav"

    # Generate 6s 1280x720 source video + 440Hz speech tone
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=6:size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source_mp4),
        ],
        check=True,
        capture_output=True,
    )

    # Generate 3s backsound track (shorter than 4.8s clip to test -stream_loop -1)
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=3",
            str(bgm_wav),
        ],
        check=True,
        capture_output=True,
    )

    download = DownloadResult(
        job_id="job_viral",
        url="https://youtu.be/synthetic",
        video_id="synthetic",
        title="Synthetic Podcast",
        duration_sec=6.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
        source_video_path=str(source_mp4),
    )
    transcript = TranscriptResult(
        job_id="job_viral",
        source="faster_whisper",
        language="id",
        segments=[
            TranscriptSegment(index=0, start=0.5, end=3.2, text="Ini hook kuat di tiga detik pertama."),
            TranscriptSegment(index=1, start=3.2, end=5.5, text="Sekarang lihat hasil edit viral lengkapnya."),
        ],
    )
    analysis = AnalysisResult(
        job_id="job_viral",
        model="gemini-4-neon",
        clips=[
            ClipCandidate(
                title="Hook Viral Podcast",
                start_time=1.0,
                end_time=5.0,
                virality_score=9,
                reason="Hook pembuka kuat dan alur jelas.",
            )
        ],
    )
    request = JobRequest(
        source_type="local_file",
        local_video_path=str(source_mp4),
        clip_count=1,
        min_duration_sec=5,
        max_duration_sec=10,
        aspect_ratio="9:16",
        padding_sec=0.4,
        framing_mode="smart_face",
        enable_subtitles=True,
        enable_hook_title=True,
        enable_opening_tts=False,
        enable_backsound=True,
        enable_viral_polish=True,
    )
    logger = JobLogger("job_viral", job_dir / "job.log")

    res = await CutterService().run(
        job_id="job_viral",
        download=download,
        analysis=analysis,
        request=request,
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
        transcript=transcript,
        settings=settings,
    )

    assert len(res.rendered_clips) == 1
    rendered = res.rendered_clips[0]
    out_mp4 = Path(rendered.output_path)
    assert out_mp4.is_file() and out_mp4.stat().st_size > 0
    assert rendered.subtitles_burned is True
    assert rendered.backsound_used == "chill_loop.wav"

    # No leftover temporary ASS or filter scripts in clips/
    clips_dir = job_dir / "clips"
    assert list(clips_dir.glob("_clip_*")) == []

    probe = subprocess.run(
        [
            preflight.ffprobe.path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(out_mp4),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(probe.stdout)
    v_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    a_stream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    assert v_stream["width"] == 1080
    assert v_stream["height"] == 1920
    assert a_stream is not None
    assert abs(float(info["format"]["duration"]) - 4.8) <= 0.4

    # Also test FitAllBlur9x16Strategy & multi-shot SplitScreen filter graph via real FFmpeg
    split_plan = _assemble_framing_plan_from_shots(
        shots=[
            ShotSegmentPlan(
                t_start=0.0,
                t_end=2.0,
                mode="split_screen",
                centers=[(0.25, 0.35), (0.75, 0.35)],
                bbox=(0.2, 0.2, 0.8, 0.5),
            ),
            ShotSegmentPlan(
                t_start=2.0,
                t_end=4.0,
                mode="fit_all_blur",
                centers=[(0.2, 0.3), (0.5, 0.3), (0.8, 0.3)],
                bbox=(0.15, 0.2, 0.85, 0.5),
            ),
        ],
        src_w=1280,
        src_h=720,
        fps=30.0,
    )
    filter_file = clips_dir / "_split_test_filter.txt"
    split_out = clips_dir / "split_test.mp4"
    filter_file.write_text(
        ";\n".join(split_plan.filter_lines + [f"[{split_plan.output_label}]format=yuv420p[vout]"]),
        encoding="utf-8",
    )
    cmd_split = build_ffmpeg_viral_cut_command(
        ffmpeg_path=Path(preflight.ffmpeg.path),
        source_path=source_mp4,
        output_path=split_out,
        padded_start_sec=0.5,
        padded_end_sec=4.5,
        filter_script_path=filter_file,
        backsound_path=None,
        audio_output_label=None,
    )
    subprocess.run(cmd_split, check=True, capture_output=True)
    probe_split = subprocess.run(
        [
            preflight.ffprobe.path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(split_out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info_split = json.loads(probe_split.stdout)
    vs = next(s for s in info_split["streams"] if s["codec_type"] == "video")
    assert vs["width"] == 1080
    assert vs["height"] == 1920
    assert abs(float(info_split["format"]["duration"]) - 4.0) <= 0.4

    # Verify FitAllBlur9x16Strategy registry
    fit_strat = get_framing_strategy("9:16", "fit_all_blur")
    assert isinstance(fit_strat, FitAllBlur9x16Strategy)
    fit_plan = await fit_strat.build_framing_plan(
        FramingContext(
            source_path=source_mp4,
            source_width=1280,
            source_height=720,
            fps=30.0,
            start_sec=0.0,
            end_sec=4.0,
            aspect_ratio="9:16",
        )
    )
    assert "boxblur=24:4" in "\n".join(fit_plan.filter_lines)


def test_analyze_shots_sync_handles_scene_cut(tmp_path: Path) -> None:
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin")
    preflight = check_startup_dependencies(settings)
    assert preflight.ffmpeg.available and preflight.ffmpeg.path

    scene_mp4 = tmp_path / "scene_cut.mp4"
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=640x360:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:r=30:d=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(scene_mp4),
        ],
        check=True,
        capture_output=True,
    )

    shots = _analyze_shots_sync(
        FramingContext(
            source_path=scene_mp4,
            source_width=640,
            source_height=360,
            fps=30.0,
            start_sec=0.0,
            end_sec=4.0,
            aspect_ratio="9:16",
        )
    )
    assert len(shots) >= 1
    assert shots[0].t_start == 0.0
    assert shots[-1].t_end == pytest.approx(4.0, abs=0.1)
