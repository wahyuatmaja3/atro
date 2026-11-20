"""Unit and real-FFmpeg integration tests for casual titles, TTS opening, and Game/Podcast formats."""

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
from atro.modules.analyzer.prompts import build_system_instruction
from atro.modules.cutter import service as cutter_service_mod
from atro.modules.cutter.framing import GameLayout9x16Strategy, get_framing_strategy
from atro.modules.cutter.framing.face_tracking import FaceBox
from atro.modules.cutter.framing.game_layout import build_game_split_filter_lines
from atro.modules.cutter.opening_narrator import clean_title_for_speech
from atro.modules.cutter.service import CutterService
from atro.modules.cutter.subtitle_burner import generate_clip_ass_file


def test_prompts_enforce_casual_titles_and_format_context() -> None:
    req_game = JobRequest(url="https://youtu.be/abc", edit_format="game")
    inst_game = build_system_instruction(req_game, 300.0)
    assert "GAME" in inst_game
    assert "kasual" in inst_game.lower()
    assert "4-8 kata" in inst_game
    assert "kaku/formal" in inst_game.lower()

    req_pod = JobRequest(url="https://youtu.be/abc", edit_format="podcast")
    inst_pod = build_system_instruction(req_pod, 300.0)
    assert "PODCAST" in inst_pod
    assert "kasual" in inst_pod.lower()


def test_clean_title_for_speech_strips_emojis_and_hashtags() -> None:
    raw = "🔥 Auto Ngamuk Gara-Gara Bocil!!?! #stumbleguys #shorts https://youtu.be/x"
    cleaned = clean_title_for_speech(raw)
    assert "#" not in cleaned
    assert "http" not in cleaned
    assert "🔥" not in cleaned
    assert cleaned.startswith("Auto Ngamuk Gara-Gara Bocil")


def test_game_split_filter_lines_with_and_without_face() -> None:
    strat = get_framing_strategy("9:16", "smart_face", edit_format="game")
    assert isinstance(strat, GameLayout9x16Strategy)

    # With streamer webcam detected in bottom-right corner
    lines_face, summary_face = build_game_split_filter_lines(
        src_w=1920,
        src_h=1080,
        face_box=FaceBox(x=0.78, y=0.65, w=0.12, h=0.22),
    )
    joined_face = "\n".join(lines_face)
    assert "scale=1080:768" in joined_face
    assert "scale=1080:1152" in joined_face
    assert "vstack=inputs=2" in joined_face
    assert "#38BDF8" in joined_face
    assert "40%" in summary_face and "60%" in summary_face

    # Without detected webcam face -> modern fit_all_blur fallback
    lines_nocam, summary_nocam = build_game_split_filter_lines(
        src_w=1920,
        src_h=1080,
        face_box=None,
    )
    assert "boxblur=24:4" in "\n".join(lines_nocam)
    assert "fit_all_blur" in summary_nocam


def test_subtitle_burner_shifts_for_opening_and_applies_game_style(tmp_path: Path) -> None:
    ass_path = tmp_path / "game.ass"
    out = generate_clip_ass_file(
        output_ass_path=ass_path,
        aspect_ratio="9:16",
        clip_duration_sec=8.0,
        clip_title="Auto Kena Mental Pas Ronde Akhir!",
        segments=[
            TranscriptSegment(index=0, start=1.0, end=4.0, text="Eh sumpah ini kocak banget!"),
        ],
        clip_start_sec=0.6,
        clip_end_sec=6.6,
        enable_subtitles=True,
        enable_hook_title=True,
        edit_format="game",
        opening_duration_sec=2.0,
        source_credit="Windah Basudara",
    )
    assert out is not None and out.is_file()
    content = out.read_text(encoding="utf-8")

    # HookTitle spans opening [0.05 -> 1.95] with light card
    assert "Dialogue: 2,0:00:00.05,0:00:01.95,HookTitle" in content
    assert "&H08F8FAFC" in content
    # ViralSub uses electric cyan highlight and is shifted by +2.0s (0.4 + 2.0 = 2.40s)
    assert "&H00F8BD38&" in content
    assert "Dialogue: 1,0:00:02.40," in content
    # Source watermark added
    assert "Source: @Windah Basudara" in content


@pytest.mark.asyncio
async def test_game_and_podcast_formats_with_tts_opening_real_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    bgm_dir = tmp_path / "backsound"
    bgm_dir.mkdir(parents=True)
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=bgm_dir,
        backsound_volume=0.15,
        enable_backsound_ducking=True,
    )
    preflight = check_startup_dependencies(settings)
    assert preflight.ok and preflight.ffmpeg.path and preflight.ffprobe.path

    job_dir = settings.workspace_dir / "job_formats"
    job_dir.mkdir(parents=True)
    source_mp4 = job_dir / "source.mp4"
    bgm_wav = bgm_dir / "lofi_bg.wav"
    tts_fixture_wav = tmp_path / "tts_fixture.wav"

    # Generate 5s 1280x720 synthetic source video + 440Hz audio
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=5:size=1280x720:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
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

    # Generate 1.8s synthetic TTS voiceover fixture and 3s BGM loop fixture
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=1.8",
            str(tts_fixture_wav),
        ],
        check=True,
        capture_output=True,
    )
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

    async def fake_synthesize_tts(text, output_audio_path, voice, rate, ffprobe_path, logger):
        dest = output_audio_path.with_suffix(".wav")
        dest.write_bytes(tts_fixture_wav.read_bytes())
        return dest, 1.8

    monkeypatch.setattr(cutter_service_mod, "synthesize_opening_voiceover", fake_synthesize_tts)

    download = DownloadResult(
        job_id="job_formats",
        url="https://youtu.be/synthetic",
        video_id="synthetic",
        title="Synthetic Stream",
        channel="GamerTest",
        duration_sec=5.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
        source_video_path=str(source_mp4),
    )
    transcript = TranscriptResult(
        job_id="job_formats",
        source="faster_whisper",
        language="id",
        segments=[
            TranscriptSegment(index=0, start=0.5, end=4.5, text="Wah parah banget momennya barusan!"),
        ],
    )
    analysis = AnalysisResult(
        job_id="job_formats",
        model="gemini-4-neon",
        clips=[
            ClipCandidate(
                title="Detik-Detik Kena Mental!",
                start_time=0.5,
                end_time=4.5,
                virality_score=9,
                reason="Reaksi kocak di akhir ronde.",
            )
        ],
    )

    for fmt in ("game", "podcast"):
        sub_job_dir = job_dir / fmt
        sub_job_dir.mkdir(parents=True, exist_ok=True)
        req = JobRequest(
            source_type="local_file",
            local_video_path=str(source_mp4),
            clip_count=1,
            min_duration_sec=5,
            max_duration_sec=10,
            aspect_ratio="9:16",
            padding_sec=0.4,
            edit_format=fmt,  # type: ignore[arg-type]
            framing_mode="smart_face",
            enable_subtitles=True,
            enable_hook_title=True,
            enable_opening_tts=True,
            enable_backsound=True,
            enable_viral_polish=True,
        )
        logger = JobLogger(f"job_{fmt}", sub_job_dir / "job.log")

        res = await CutterService().run(
            job_id=f"job_{fmt}",
            download=download,
            analysis=analysis,
            request=req,
            job_dir=sub_job_dir,
            preflight=preflight,
            logger=logger,
            transcript=transcript,
            settings=settings,
        )

        assert len(res.rendered_clips) == 1
        rendered = res.rendered_clips[0]
        assert rendered.edit_format == fmt
        assert rendered.opening_tts_used is True
        assert rendered.intro_duration_sec == 1.8
        assert rendered.subtitles_burned is True
        assert rendered.backsound_used == "lofi_bg.wav"

        clips_dir = sub_job_dir / "clips"
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
                rendered.output_path,
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
        # Expected duration: padded [0.1 -> 4.9] = 4.8s + 1.8s intro = 6.6s
        assert abs(float(info["format"]["duration"]) - 6.6) <= 0.5
