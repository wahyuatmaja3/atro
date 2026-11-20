"""Unit tests for DownloaderService command isolation and subtitle HTTP 429 resilience."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atro.core.logging_utils import JobLogger
from atro.core.schemas import PreflightBinaryInfo, PreflightReport
from atro.modules.downloader import service as dl_mod
from atro.modules.downloader.service import (
    DownloaderService,
    _build_subtitle_download_cmd,
    _build_video_download_cmd,
    _select_best_subtitle_target,
)


def test_select_best_subtitle_target_prefers_manual_and_exact_single_lang() -> None:
    # Auto captions with id-orig, id, en -> pick exact 'id' only
    info_auto = {
        "language": "id",
        "subtitles": {},
        "automatic_captions": {
            "id-orig": [{"ext": "vtt"}],
            "id": [{"ext": "vtt"}],
            "en": [{"ext": "vtt"}],
        },
    }
    assert _select_best_subtitle_target(info_auto) == ("id", True)

    # Manual subtitles beat automatic_captions
    info_manual = {
        "language": "id",
        "subtitles": {"en": [{"ext": "vtt"}]},
        "automatic_captions": {"id": [{"ext": "vtt"}]},
    }
    assert _select_best_subtitle_target(info_manual) == ("en", False)

    # None when no matching languages
    info_none = {"language": "ja", "subtitles": {}, "automatic_captions": {"fr": []}}
    assert _select_best_subtitle_target(info_none) == (None, False)


def test_build_video_download_cmd_has_no_subtitle_or_paths_flags() -> None:
    cmd = _build_video_download_cmd(
        yt_dlp_bin="yt-dlp",
        ffmpeg_dir=Path("C:/ffmpeg/bin"),
        url="https://www.youtube.com/watch?v=Sy_S4TgvKbY",
        js_runtime_args=["--js-runtimes", "node"],
    )
    assert "--write-subs" not in cmd
    assert "--write-auto-subs" not in cmd
    assert "--sub-langs" not in cmd
    assert "--paths" not in cmd
    assert "-P" not in cmd
    idx = cmd.index("-o")
    assert cmd[idx + 1] == "source.%(ext)s"
    assert "--js-runtimes" in cmd

    sub_cmd = _build_subtitle_download_cmd(
        yt_dlp_bin="yt-dlp",
        url="https://www.youtube.com/watch?v=Sy_S4TgvKbY",
        lang="id",
        is_auto=True,
        js_runtime_args=["--js-runtimes", "node"],
    )
    assert "--skip-download" in sub_cmd
    assert "--write-auto-subs" in sub_cmd
    assert "--write-subs" not in sub_cmd
    assert sub_cmd[sub_cmd.index("--sub-langs") + 1] == "id"


@pytest.mark.asyncio
async def test_downloader_succeeds_when_subtitle_hits_http_429(tmp_path: Path, monkeypatch) -> None:
    job_dir = tmp_path / "job_429"
    job_dir.mkdir(parents=True)
    logger = JobLogger("job_429", job_dir / "job.log")

    preflight = PreflightReport(
        ok=True,
        yt_dlp=PreflightBinaryInfo(name="yt-dlp", available=True, path="yt-dlp", version="2026.06.09"),
        ffmpeg=PreflightBinaryInfo(name="ffmpeg", available=True, path="C:/ffmpeg/bin/ffmpeg.exe", version="8.1.1"),
        ffprobe=PreflightBinaryInfo(name="ffprobe", available=True, path="C:/ffmpeg/bin/ffprobe.exe", version="8.1.1"),
    )

    yt_info = {
        "id": "Sy_S4TgvKbY",
        "title": "Test Podcast",
        "uploader": "Test Channel",
        "duration": 125.0,
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "language": "id",
        "subtitles": {},
        "automatic_captions": {
            "id-orig": [{"ext": "vtt"}],
            "id": [{"ext": "vtt"}],
            "en": [{"ext": "vtt"}],
        },
    }
    ffprobe_json = {
        "format": {"duration": "125.5"},
        "streams": [
            {"codec_type": "video", "width": 1280, "height": 720, "avg_frame_rate": "30/1"},
            {"codec_type": "audio"},
        ],
    }

    async def fake_capture(cmd: list[str], timeout=30.0, cwd=None):
        if "--dump-single-json" in cmd:
            return 0, json.dumps(yt_info), ""
        if "ffprobe" in cmd[0]:
            return 0, json.dumps(ffprobe_json), ""
        return 1, "", "unexpected command"

    async def fake_streaming(cmd: list[str], on_line=None, cancel_event=None, cwd=None):
        if "--skip-download" in cmd:
            # Simulate YouTube HTTP 429 rate limit on subtitle request
            return 1, ["ERROR: Unable to download video subtitles for 'id': HTTP Error 429: Too Many Requests"]
        # Video download succeeds and writes source.mp4 in cwd (job_dir)
        assert cwd == job_dir
        (job_dir / "source.mp4").write_bytes(b"fake-mp4-data")
        if on_line:
            await on_line("[download] 100.0% of 10.00MiB")
        return 0, ["[download] 100%"]

    monkeypatch.setattr(dl_mod, "run_subprocess_capture", fake_capture)
    monkeypatch.setattr(dl_mod, "run_subprocess_streaming", fake_streaming)
    monkeypatch.setattr(dl_mod, "_detect_js_runtime_args", lambda: ["--js-runtimes", "node"])

    service = DownloaderService()
    result = await service.run(
        job_id="job_429",
        url="https://www.youtube.com/watch?v=Sy_S4TgvKbY",
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
    )

    assert result.video_id == "Sy_S4TgvKbY"
    assert Path(result.source_video_path).is_file()
    assert result.subtitle_candidates == []
    assert (job_dir / "metadata.json").is_file()
    log_text = (job_dir / "job.log").read_text(encoding="utf-8")
    assert "faster-whisper" in log_text


@pytest.mark.asyncio
async def test_downloader_retries_fallback_client_on_http_403(tmp_path: Path, monkeypatch) -> None:
    job_dir = tmp_path / "job_403"
    job_dir.mkdir(parents=True)
    logger = JobLogger("job_403", job_dir / "job.log")

    preflight = PreflightReport(
        ok=True,
        yt_dlp=PreflightBinaryInfo(name="yt-dlp", available=True, path="yt-dlp", version="2026.06.09"),
        ffmpeg=PreflightBinaryInfo(name="ffmpeg", available=True, path="C:/ffmpeg/bin/ffmpeg.exe", version="8.1.1"),
        ffprobe=PreflightBinaryInfo(name="ffprobe", available=True, path="C:/ffmpeg/bin/ffprobe.exe", version="8.1.1"),
    )

    yt_info = {
        "id": "Sy_S4TgvKbY",
        "title": "Test Podcast 403",
        "duration": 90.0,
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "language": "id",
        "subtitles": {},
        "automatic_captions": {},
    }
    ffprobe_json = {
        "format": {"duration": "90.0"},
        "streams": [
            {"codec_type": "video", "width": 1280, "height": 720, "avg_frame_rate": "30/1"},
            {"codec_type": "audio"},
        ],
    }

    async def fake_capture(cmd: list[str], timeout=30.0, cwd=None):
        if "--dump-single-json" in cmd:
            return 0, json.dumps(yt_info), ""
        if "ffprobe" in cmd[0]:
            return 0, json.dumps(ffprobe_json), ""
        return 1, "", "unexpected command"

    attempts: list[list[str]] = []

    async def fake_streaming(cmd: list[str], on_line=None, cancel_event=None, cwd=None):
        attempts.append(cmd)
        if len(attempts) == 1:
            return 1, ["ERROR: unable to download video data: HTTP Error 403: Forbidden"]
        assert "--extractor-args" in cmd
        assert "youtube:player_client=android,tv_embedded" in cmd
        (job_dir / "source.mp4").write_bytes(b"ok-mp4")
        return 0, ["[download] 100%"]

    monkeypatch.setattr(dl_mod, "run_subprocess_capture", fake_capture)
    monkeypatch.setattr(dl_mod, "run_subprocess_streaming", fake_streaming)
    monkeypatch.setattr(dl_mod, "_detect_js_runtime_args", lambda: ["--js-runtimes", "node"])

    service = DownloaderService()
    result = await service.run(
        job_id="job_403",
        url="https://www.youtube.com/watch?v=Sy_S4TgvKbY",
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
    )

    assert len(attempts) == 2
    assert Path(result.source_video_path).is_file()


@pytest.mark.asyncio
async def test_ingest_local_file_with_real_ffmpeg(tmp_path: Path) -> None:
    import subprocess

    from atro.config import AppSettings
    from atro.core.preflight import check_startup_dependencies

    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin")
    preflight = check_startup_dependencies(settings)
    assert preflight.ffmpeg.available and preflight.ffmpeg.path and preflight.ffprobe.path

    external_dir = tmp_path / "external_downloads"
    external_dir.mkdir(parents=True)
    local_mp4 = external_dir / "my_podcast.mp4"
    local_vtt = external_dir / "my_podcast.vtt"
    local_vtt.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nHalo ini uji video lokal.\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=6:size=640x360:rate=30",
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
            str(local_mp4),
        ],
        check=True,
        capture_output=True,
    )

    job_dir = tmp_path / "ws" / "job_local"
    logger = JobLogger("job_local", job_dir / "job.log")

    result = await DownloaderService().ingest_local_file(
        job_id="job_local",
        local_video_path=local_mp4,
        local_subtitle_path=local_vtt,
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
    )

    assert result.title == "my_podcast"
    assert result.channel == "Local File"
    assert result.width == 640
    assert result.height == 360
    assert abs(result.duration_sec - 6.0) <= 0.4
    assert result.has_audio is True
    assert Path(result.source_video_path).is_file()
    assert len(result.subtitle_candidates) == 1
    assert result.subtitle_candidates[0].lang == "local"
    assert Path(result.subtitle_candidates[0].local_path).is_file()
    assert (job_dir / "metadata.json").is_file()
