"""Tests for Whisper typo cleanup, AI transcript proofreading, direct title filenames, and casual clip titles."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies
from atro.core.schemas import (
    AnalysisResult,
    ClipCandidate,
    DownloadResult,
    GeminiClipItem,
    GeminiClipSelectionSchema,
    JobRequest,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)
from atro.modules.analyzer.prompts import build_system_instruction, build_user_prompt
from atro.modules.analyzer.validator import validate_and_sanitize_clips
from atro.modules.cutter.service import (
    CutterService,
    resolve_clip_output_path,
    sanitize_clip_filename_stem,
)
from atro.modules.transcriber import proofreader as pr_mod
from atro.modules.transcriber.proofreader import (
    align_words_preserving_timestamps,
    build_whisper_hotwords,
    build_whisper_initial_prompt,
    clean_transcript_segments_offline,
    infer_language_hint,
    proofread_transcript_segments_with_llm,
)
from tests.test_composer_studio import ensure_qt_gui_app


def test_infer_language_hint_and_whisper_prompt_generation() -> None:
    assert (
        infer_language_hint(
            None,
            title="Ketika Raditya Dika Ikut Kesel Sama Teman Kampus",
            channel="Podcast Seru",
        )
        == "id"
    )
    assert infer_language_hint("id-ID", title="Random Title") == "id"
    assert infer_language_hint(None, title="Daily Vlog Tokyo Walk", channel="Walker") is None
    assert infer_language_hint(None, title="Daily Vlog Tokyo Walk", explicit_pref="en") == "en"

    prompt = build_whisper_initial_prompt("Podcast Tas 200 Juta", "Raditya Dika", "id")
    assert "Raditya Dika" in prompt
    assert "nggak" in prompt
    hot = build_whisper_hotwords("Ketika Raditya Dika Ikut Kesel", "PodHub ID")
    assert hot is not None and "Raditya" in hot and "PodHub" in hot


def test_offline_transcript_cleaner_fixes_typos_and_preserves_word_timestamps() -> None:
    segs = [
        TranscriptSegment(
            index=0,
            start=0.0,
            end=3.2,
            text="radit ya ikut kesel krn podkas yg ngga jelas , ya ya ya ya",
            words=[
                TranscriptWord(word="radit", start=0.1, end=0.4),
                TranscriptWord(word="ya", start=0.4, end=0.6),
                TranscriptWord(word="ikut", start=0.7, end=1.0),
                TranscriptWord(word="kesel", start=1.0, end=1.4),
                TranscriptWord(word="krn", start=1.4, end=1.6),
                TranscriptWord(word="podkas", start=1.6, end=2.0),
                TranscriptWord(word="yg", start=2.0, end=2.2),
                TranscriptWord(word="ngga", start=2.2, end=2.5),
                TranscriptWord(word="jelas", start=2.5, end=2.8),
                TranscriptWord(word=",", start=2.8, end=2.85),
                TranscriptWord(word="ya", start=2.86, end=2.94),
                TranscriptWord(word="ya", start=2.95, end=3.02),
                TranscriptWord(word="ya", start=3.03, end=3.10),
                TranscriptWord(word="ya", start=3.11, end=3.18),
            ],
        ),
        TranscriptSegment(
            index=1,
            start=3.3,
            end=4.5,
            text="Terima kasih telah menonton",
            words=[],
        ),
        TranscriptSegment(
            index=2,
            start=4.6,
            end=6.0,
            text="kode GA bgt di yutub",
            words=[],
        ),
    ]

    cleaned = clean_transcript_segments_offline(
        segs,
        title="Raditya Dika Kesel Di Podcast",
        channel="Podcast",
    )
    assert len(cleaned) == 2  # Hallucination segment removed
    s0 = cleaned[0]
    assert "Raditya" in s0.text
    assert "karena" in s0.text
    assert "podcast" in s0.text
    assert "yang nggak jelas," in s0.text
    assert "ya ya ya" not in s0.text
    # Word timestamps remain strictly monotonic and bounded
    assert s0.words and s0.words[0].word == "Raditya"
    assert 0.0 <= s0.words[0].start < s0.words[-1].end <= 3.2
    for prev_w, cur_w in zip(s0.words, s0.words[1:]):
        assert cur_w.start >= prev_w.end

    # Short uppercase acronym GA is not turned into GAK
    assert "GA" in cleaned[1].text
    assert "banget" in cleaned[1].text
    assert "YouTube" in cleaned[1].text


@pytest.mark.asyncio
async def test_llm_transcript_proofreader_fixes_typos_safely_and_aligns_words(
    tmp_path: Path, monkeypatch
) -> None:
    from atro.modules.analyzer import openai_compat as compat_mod

    def fake_post(url, headers=None, json=None, timeout=None):
        body = {
            "corrections": [
                {"index": 0, "text": "Pas dia bawa tas 200 juta ke Sila sih parah banget."},
                # Unsafe hallucinated summary on index 1 -> must be rejected by guard
                {"index": 1, "text": "Ringkasan singkat."},
            ]
        }
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": json_module_dumps(body)}}]},
            text="",
        )

    import json as json_mod

    json_module_dumps = json_mod.dumps
    monkeypatch.setattr(compat_mod.requests, "post", fake_post)

    settings = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="http://localhost:11434/v1",
        openai_model="qwen2.5:7b",
        enable_transcript_proofread=True,
    )
    dl = DownloadResult(
        job_id="j_pr",
        video_id="v1",
        title="Tas 200 Juta Bikin Kesel Ke Sila",
        channel="PodHub",
        duration_sec=30.0,
        width=1280,
        height=720,
        fps=30.0,
        source_video_path=str(tmp_path / "src.mp4"),
    )
    segs = [
        TranscriptSegment(
            index=0,
            start=1.0,
            end=4.0,
            text="Pas dia bawa tas 200 juta ke silah sih parah banget.",
            words=[
                TranscriptWord(word="Pas", start=1.0, end=1.2),
                TranscriptWord(word="dia", start=1.2, end=1.4),
                TranscriptWord(word="bawa", start=1.4, end=1.7),
                TranscriptWord(word="tas", start=1.7, end=2.0),
                TranscriptWord(word="200", start=2.0, end=2.3),
                TranscriptWord(word="juta", start=2.3, end=2.6),
                TranscriptWord(word="ke", start=2.6, end=2.8),
                TranscriptWord(word="silah", start=2.8, end=3.2),
                TranscriptWord(word="sih", start=3.2, end=3.4),
                TranscriptWord(word="parah", start=3.4, end=3.7),
                TranscriptWord(word="banget.", start=3.7, end=4.0),
            ],
        ),
        TranscriptSegment(
            index=1,
            start=4.5,
            end=8.5,
            text="Terus gue bilang jangan nekat kayak gitu lagi di kampus dong bro!",
            words=[],
        ),
    ]
    out = await proofread_transcript_segments_with_llm(
        segs, download=dl, settings=settings, logger=JobLogger("j_pr", tmp_path / "log.txt")
    )
    assert out[0].text == "Pas dia bawa tas 200 juta ke Sila sih parah banget."
    sila_w = [w for w in out[0].words if w.word == "Sila"]
    assert len(sila_w) == 1 and sila_w[0].start == pytest.approx(2.8, abs=0.05)
    # Segment 1 kept original text because LLM returned an unsafe short summary
    assert "kampus dong bro!" in out[1].text


def test_direct_clip_filename_without_clip_prefix_and_collision_handling(tmp_path: Path) -> None:
    assert sanitize_clip_filename_stem("Beli Tas 200 Juta? Auto Kesel!") == "Beli Tas 200 Juta Auto Kesel!"
    assert sanitize_clip_filename_stem("CON") == "CON (klip)"
    assert not sanitize_clip_filename_stem("Judul Keren").startswith("clip_")

    clips_dir = tmp_path / "clips"
    clips_dir.mkdir(parents=True)
    reserved: set[Path] = set()
    p1 = resolve_clip_output_path(clips_dir, "Bikin Kesel Satu Studio!", clip_index=1, reserved_paths=reserved)
    reserved.add(p1.resolve())
    p2 = resolve_clip_output_path(clips_dir, "Bikin Kesel Satu Studio!", clip_index=2, reserved_paths=reserved)
    assert p1.name == "Bikin Kesel Satu Studio!.mp4"
    assert p2.name == "Bikin Kesel Satu Studio! (2).mp4"


@pytest.mark.asyncio
async def test_cutter_outputs_direct_title_filename_real_ffmpeg(tmp_path: Path) -> None:
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin", backsound_dir=tmp_path / "bgm")
    preflight = check_startup_dependencies(settings)
    assert preflight.ok and preflight.ffmpeg.path

    job_dir = settings.workspace_dir / "job_fn"
    job_dir.mkdir(parents=True)
    source_mp4 = job_dir / "source.mp4"
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=640x360:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
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
    dl = DownloadResult(
        job_id="job_fn",
        video_id="fn1",
        title="Video Asli",
        source_video_path=str(source_mp4),
        duration_sec=3.0,
        width=640,
        height=360,
        fps=24.0,
        has_audio=True,
    )
    req = JobRequest(
        source_type="local_file",
        local_video_path=str(source_mp4),
        clip_count=1,
        min_duration_sec=5,
        max_duration_sec=10,
        padding_sec=0.3,
        enable_subtitles=False,
        enable_hook_title=False,
        enable_opening_tts=False,
        enable_backsound=False,
        enable_viral_polish=False,
    )
    analysis = AnalysisResult(
        job_id="job_fn",
        model="test",
        clips=[
            ClipCandidate(
                title="Ketauan Bawa Tas 200 Juta!",
                start_time=0.0,
                end_time=2.5,
                virality_score=9,
                reason="Lucu banget",
            )
        ],
    )
    render_res = await CutterService().run(
        job_id="job_fn",
        download=dl,
        analysis=analysis,
        request=req,
        job_dir=job_dir,
        preflight=preflight,
        logger=JobLogger("job_fn", job_dir / "log.txt"),
        transcript=None,
        settings=settings,
    )
    out_file = Path(render_res.rendered_clips[0].output_path)
    assert out_file.is_file()
    assert out_file.name == "Ketauan Bawa Tas 200 Juta!.mp4"
    assert not out_file.name.startswith("clip_01")


def test_prompts_enforce_nonformal_creative_video_specific_titles_and_settings_ui() -> None:
    from atro.ui.widgets.settings_dialog import SettingsDialog

    req = JobRequest(url="https://youtu.be/abc", edit_format="podcast")
    sys_p = build_system_instruction(req, 120.0)
    assert "NONFORMAL" in sys_p
    assert "SPESIFIK SESUAI ISI VIDEO" in sys_p
    assert "VARIASI KREATIF" in sys_p
    usr_p = build_user_prompt(
        DownloadResult(
            job_id="j",
            video_id="v",
            title="Podcast Kampus",
            duration_sec=60.0,
            width=1280,
            height=720,
            fps=30.0,
            source_video_path="a.mp4",
        ),
        "[0.0 - 5.0] Bawa tas 200 juta",
    )
    assert "nonformal" in usr_p.lower()

    raw = GeminiClipSelectionSchema(
        clips=[
            GeminiClipItem(
                title='Judul: "**Gila Sih Bawa Tas 200 Juta Ke Kampus!**" #shorts',
                start_time=0.0,
                end_time=12.0,
                virality_score=9,
                reason="Kontras gokil",
            )
        ]
    )
    clips, _ = validate_and_sanitize_clips(raw, 60.0, req)
    assert clips[0].title == "Gila Sih Bawa Tas 200 Juta Ke Kampus!"

    ensure_qt_gui_app()
    s = AppSettings(whisper_language="id", enable_transcript_proofread=True)
    dlg = SettingsDialog(s)
    try:
        assert dlg.whisper_lang_combo.currentData() == "id"
        assert dlg.proofread_chk.isChecked() is True
        dlg.whisper_lang_combo.setCurrentIndex(dlg.whisper_lang_combo.findData("en"))
        dlg.proofread_chk.setChecked(False)
        dlg._on_save()
        updated = dlg.updated_settings()
        assert updated.whisper_language == "en"
        assert updated.enable_transcript_proofread is False
    finally:
        dlg.close()
        dlg.deleteLater()
