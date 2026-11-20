"""Unit and real-FFmpeg integration tests for CutterService and framing strategies."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies
from atro.core.schemas import AnalysisResult, ClipCandidate, DownloadResult, JobRequest
from atro.modules.cutter.framing import (
    CenterCrop9x16Strategy,
    FaceTracking9x16Strategy,
    FramingContext,
    Original16x9Strategy,
    get_framing_strategy,
)
from atro.modules.cutter.service import CutterService, compute_padded_window


def test_compute_padded_window_clamps_bounds() -> None:
    assert compute_padded_window(0.2, 10.0, 0.4, 120.0) == (0.0, 10.4)
    assert compute_padded_window(110.0, 119.8, 0.4, 120.0) == (109.6, 120.0)
    assert compute_padded_window(15.0, 45.0, 0.4, 120.0) == (14.6, 45.4)


@pytest.mark.asyncio
async def test_framing_strategies_filters(tmp_path: Path) -> None:
    ctx_16_9 = FramingContext(
        source_path=tmp_path / "src.mp4",
        source_width=1920,
        source_height=1080,
        fps=30.0,
        start_sec=0.0,
        end_sec=10.0,
        aspect_ratio="16:9",
    )
    strat_16_9 = get_framing_strategy("16:9", "center_crop")
    assert isinstance(strat_16_9, Original16x9Strategy)
    assert "setsar=1" in await strat_16_9.build_video_filter(ctx_16_9)

    # Landscape 1920x1080 -> 9:16 center crop
    ctx_9_16 = FramingContext(
        source_path=tmp_path / "src.mp4",
        source_width=1920,
        source_height=1080,
        fps=30.0,
        start_sec=0.0,
        end_sec=10.0,
        aspect_ratio="9:16",
    )
    strat_9_16 = get_framing_strategy("9:16", "center_crop")
    assert isinstance(strat_9_16, CenterCrop9x16Strategy)
    vf = await strat_9_16.build_video_filter(ctx_9_16)
    assert vf == "crop=606:1080:657:0,scale=1080:1920:flags=lanczos,setsar=1"

    # Portrait 720x1280 -> 9:16 center crop
    cw, ch, cx, cy = CenterCrop9x16Strategy.compute_crop_rect(720, 1280)
    assert (cw, ch, cx, cy) == (720, 1280, 0, 0)

    # Face tracking extension point fallback
    strat_ft = get_framing_strategy("9:16", "face_tracking")
    assert isinstance(strat_ft, FaceTracking9x16Strategy)
    assert await strat_ft.build_video_filter(ctx_9_16) == vf


@pytest.mark.asyncio
async def test_cutter_integration_real_ffmpeg(tmp_path: Path) -> None:
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin")
    preflight = check_startup_dependencies(settings)
    assert preflight.ok and preflight.ffmpeg.path and preflight.ffprobe.path

    job_dir = tmp_path / "job_01"
    job_dir.mkdir(parents=True)
    source_mp4 = job_dir / "source.mp4"

    # Generate synthetic 10s 1280x720 MP4 with 440Hz audio tone
    gen_cmd = [
        preflight.ffmpeg.path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=10:size=1280x720:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=10",
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
    ]
    subprocess.run(gen_cmd, check=True, capture_output=True)

    download = DownloadResult(
        job_id="job_01",
        url="https://youtu.be/synthetic",
        video_id="synthetic",
        title="Synthetic Test",
        duration_sec=10.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
        source_video_path=str(source_mp4),
    )
    analysis = AnalysisResult(
        job_id="job_01",
        model="gemini-4-neon",
        clips=[
            ClipCandidate(
                title="Segmen Uji",
                start_time=2.0,
                end_time=6.0,
                virality_score=9,
                reason="Test akurasi potong.",
            )
        ],
    )
    logger = JobLogger("job_01", job_dir / "job.log")
    cutter = CutterService()

    # 1. Test 9:16 center-crop with 0.4s padding -> padded [1.6, 6.4] = 4.8s
    req_916 = JobRequest(
        url="https://youtu.be/synthetic",
        clip_count=1,
        min_duration_sec=5,
        max_duration_sec=10,
        aspect_ratio="9:16",
        padding_sec=0.4,
        enable_opening_tts=False,
    )
    res_916 = await cutter.run(
        job_id="job_01",
        download=download,
        analysis=analysis,
        request=req_916,
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
    )
    assert len(res_916.rendered_clips) == 1
    clip_path_916 = Path(res_916.rendered_clips[0].output_path)
    assert clip_path_916.is_file()

    probe_916 = subprocess.run(
        [
            preflight.ffprobe.path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(clip_path_916),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info_916 = json.loads(probe_916.stdout)
    streams_916 = info_916["streams"]
    v_stream = next(s for s in streams_916 if s["codec_type"] == "video")
    a_stream = next((s for s in streams_916 if s["codec_type"] == "audio"), None)
    assert v_stream["width"] == 1080
    assert v_stream["height"] == 1920
    assert a_stream is not None
    dur_916 = float(info_916["format"]["duration"])
    assert abs(dur_916 - 4.8) <= 0.35

    # 2. Test 16:9 passthrough
    req_169 = JobRequest(
        url="https://youtu.be/synthetic",
        clip_count=1,
        min_duration_sec=5,
        max_duration_sec=10,
        aspect_ratio="16:9",
        padding_sec=0.4,
        enable_opening_tts=False,
    )
    res_169 = await cutter.run(
        job_id="job_01",
        download=download,
        analysis=analysis,
        request=req_169,
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
    )
    clip_path_169 = Path(res_169.rendered_clips[0].output_path)
    probe_169 = subprocess.run(
        [
            preflight.ffprobe.path,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(clip_path_169),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info_169 = json.loads(probe_169.stdout)
    v_stream_169 = next(s for s in info_169["streams"] if s["codec_type"] == "video")
    assert v_stream_169["width"] == 1280
    assert v_stream_169["height"] == 720
    dur_169 = float(info_169["format"]["duration"])
    assert abs(dur_169 - 4.8) <= 0.35
