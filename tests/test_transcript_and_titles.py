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
    proofread_clip_segments_with_llm,
    proofread_transcript_segments_with_llm,
)
from atro.core.subprocess_utils import JobCancelledError
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


def test_transcript_proofread_defaults_off_to_prevent_unexpected_token_drain(tmp_path: Path) -> None:
    s = AppSettings(workspace_dir=tmp_path, bin_dir=tmp_path, backsound_dir=tmp_path)
    assert s.enable_transcript_proofread is False


@pytest.mark.asyncio
async def test_llm_proofreader_aborts_immediately_on_402_and_timeout_and_honors_cancel(
    tmp_path: Path, monkeypatch
) -> None:
    import asyncio
    import requests
    from atro.modules.analyzer import openai_compat as compat_mod

    compat_mod.clear_response_format_cache()
    dl = DownloadResult(
        job_id="j_abort",
        video_id="v1",
        title="Podcast Test",
        channel="PodHub",
        duration_sec=120.0,
        width=1280,
        height=720,
        fps=30.0,
        source_video_path=str(tmp_path / "src.mp4"),
    )
    settings = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="sk-test",
        openai_model="gpt-4o-mini",
        enable_transcript_proofread=True,
    )
    # 30 segments across 3 batches of batch_size=10
    segs = [
        TranscriptSegment(
            index=i,
            start=float(i * 2),
            end=float(i * 2 + 1.5),
            text=f"Kalimat ke {i} di podcast ini.",
        )
        for i in range(30)
    ]

    # 1) HTTP 402 Insufficient Balance -> stops after 1 call, NOT 3 calls!
    calls_402 = 0

    def fake_post_402(url, headers=None, json=None, timeout=None):
        nonlocal calls_402
        calls_402 += 1
        return SimpleNamespace(
            status_code=402,
            text='{"error":{"message":"Insufficient Balance","type":"insufficient_balance_error"}}',
        )

    monkeypatch.setattr(compat_mod.requests, "post", fake_post_402)
    await proofread_transcript_segments_with_llm(
        segs,
        download=dl,
        settings=settings,
        logger=JobLogger("j_402", tmp_path / "log_402.txt"),
        batch_size=10,
    )
    assert calls_402 == 1

    # 2) Timeout -> stops after 1 call instead of draining tokens on subsequent timed-out batches
    calls_to = 0

    def fake_post_timeout(url, headers=None, json=None, timeout=None):
        nonlocal calls_to
        calls_to += 1
        raise requests.ReadTimeout("timed out")

    monkeypatch.setattr(compat_mod.requests, "post", fake_post_timeout)
    await proofread_transcript_segments_with_llm(
        segs,
        download=dl,
        settings=settings,
        logger=JobLogger("j_to", tmp_path / "log_to.txt"),
        batch_size=10,
    )
    assert calls_to == 1

    # 3) Cancel event cancels cleanly and progress callback fires
    cancel_ev = asyncio.Event()
    prog_values: list[float] = []
    calls_cancel = 0

    def fake_post_ok(url, headers=None, json=None, timeout=None):
        nonlocal calls_cancel
        calls_cancel += 1
        cancel_ev.set()
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": '{"corrections": []}'}}]},
            text="",
        )

    monkeypatch.setattr(compat_mod.requests, "post", fake_post_ok)
    with pytest.raises(JobCancelledError):
        await proofread_transcript_segments_with_llm(
            segs,
            download=dl,
            settings=settings,
            logger=JobLogger("j_can", tmp_path / "log_can.txt"),
            batch_size=10,
            cancel_event=cancel_ev,
            on_progress=prog_values.append,
        )
    assert calls_cancel == 1
    assert prog_values and prog_values[0] > 0.0


@pytest.mark.asyncio
async def test_clip_scoped_proofreader_only_sends_selected_clip_segments(
    tmp_path: Path, monkeypatch
) -> None:
    from atro.modules.analyzer import openai_compat as compat_mod

    sent_prompts: list[str] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent_prompts.append((json or {})["messages"][1]["content"])
        body = {"corrections": [{"index": 2, "text": "Pas dia bawa tas 200 juta ke Sila."}]}
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": json_mod.dumps(body)}}]},
            text="",
        )

    import json as json_mod

    monkeypatch.setattr(compat_mod.requests, "post", fake_post)
    dl = DownloadResult(
        job_id="j_clip_pr",
        video_id="v1",
        title="Tas 200 Juta Ke Sila",
        channel="PodHub",
        duration_sec=300.0,
        width=1280,
        height=720,
        fps=30.0,
        source_video_path=str(tmp_path / "src.mp4"),
    )
    settings = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="http://localhost:11434/v1",
        openai_model="qwen2.5:7b",
        enable_transcript_proofread=True,
    )
    segs = [
        TranscriptSegment(index=0, start=0.0, end=5.0, text="Segmen luar klip awal."),
        TranscriptSegment(index=1, start=10.0, end=15.0, text="Segmen luar klip juga."),
        TranscriptSegment(index=2, start=50.0, end=55.0, text="Pas dia bawa tas 200 juta ke silah."),
        TranscriptSegment(index=3, start=180.0, end=185.0, text="Segmen jauh di luar klip."),
    ]
    clips = [
        ClipCandidate(
            title="Bawa Tas 200 Juta!",
            start_time=48.0,
            end_time=60.0,
            virality_score=9,
            reason="Hook mantap.",
        )
    ]

    out = await proofread_clip_segments_with_llm(
        segs,
        clips=clips,
        download=dl,
        settings=settings,
        logger=JobLogger("j_clip_pr", tmp_path / "log.txt"),
    )
    assert len(sent_prompts) == 1
    assert "[2]" in sent_prompts[0]
    assert "[0]" not in sent_prompts[0]
    assert "[3]" not in sent_prompts[0]
    assert out[2].text == "Pas dia bawa tas 200 juta ke Sila."
    assert out[0].text == "Segmen luar klip awal."


@pytest.mark.asyncio
async def test_transcriber_service_saves_transcript_without_blocking_on_llm_proofread(
    tmp_path: Path, monkeypatch
) -> None:
    from atro.modules.analyzer import openai_compat as compat_mod
    from atro.modules.transcriber import service as tr_svc_mod
    from atro.modules.transcriber.service import TranscriberService

    api_calls = 0

    def fail_if_called(*args, **kwargs):
        nonlocal api_calls
        api_calls += 1
        raise AssertionError("TranscriberService tidak boleh memanggil LLM API saat transkripsi.")

    monkeypatch.setattr(compat_mod.requests, "post", fail_if_called)

    job_dir = tmp_path / "job_tr"
    job_dir.mkdir(parents=True)
    src_mp4 = job_dir / "source.mp4"
    src_mp4.write_bytes(b"\x00")
    sub_vtt = job_dir / "auto.id.vtt"
    sub_vtt.write_text(
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:04.000\n"
        "ini podkas yg seru bgt\n\n"
        "00:00:05.000 --> 00:00:09.000\n"
        "pembahasan lanjutan yg jelas\n\n"
        "00:00:10.000 --> 00:00:14.000\n"
        "penutup episode hari ini\n",
        encoding="utf-8",
    )
    from atro.core.schemas import PreflightBinaryInfo, PreflightReport, SubtitleCandidate

    dl = DownloadResult(
        job_id="job_tr",
        video_id="v1",
        title="Podcast Seru",
        duration_sec=16.0,
        width=1280,
        height=720,
        fps=30.0,
        source_video_path=str(src_mp4),
        subtitle_candidates=[
            SubtitleCandidate(lang="id", is_auto_generated=True, ext="vtt", local_path=str(sub_vtt))
        ],
    )
    settings = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="sk-test",
        openai_model="gpt-4o-mini",
        enable_transcript_proofread=True,
        workspace_dir=tmp_path,
        bin_dir=tmp_path,
    )
    preflight = PreflightReport(
        ok=True,
        yt_dlp=PreflightBinaryInfo(name="yt-dlp", available=True, path="yt-dlp"),
        ffmpeg=PreflightBinaryInfo(name="ffmpeg", available=True, path="ffmpeg"),
        ffprobe=PreflightBinaryInfo(name="ffprobe", available=True, path="ffprobe"),
    )
    progress_seen: list[float] = []
    res = await TranscriberService().run(
        job_id="job_tr",
        download=dl,
        job_dir=job_dir,
        settings=settings,
        preflight=preflight,
        logger=JobLogger("job_tr", job_dir / "job.log"),
        on_progress=progress_seen.append,
    )
    assert api_calls == 0
    assert (job_dir / "transcript.json").is_file()
    assert progress_seen and progress_seen[-1] == 100.0
    assert "podcast" in res.segments[0].text.lower()
    assert "banget" in res.segments[0].text.lower()


@pytest.mark.asyncio
async def test_orchestrator_saves_transcript_at_55_pct_and_proofreads_only_clips(
    tmp_path: Path, monkeypatch
) -> None:
    from atro.core.schemas import JobStatus, PreflightBinaryInfo, PreflightReport, RenderResult
    from atro.modules.analyzer import openai_compat as compat_mod
    from atro.pipeline.orchestrator import PipelineOrchestrator
    from atro.pipeline.state_store import create_job_state

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)
    src_mp4 = tmp_path / "source.mp4"
    src_mp4.write_bytes(b"\x00")

    req = JobRequest(source_type="local_file", local_video_path=str(src_mp4), clip_count=1)
    state = create_job_state(ws_dir, req)
    job_dir = Path(state.workspace_dir)

    settings = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="sk-test",
        openai_model="gpt-4o-mini",
        enable_transcript_proofread=True,
        workspace_dir=ws_dir,
        bin_dir=tmp_path,
        backsound_dir=tmp_path,
    )
    preflight = PreflightReport(
        ok=True,
        yt_dlp=PreflightBinaryInfo(name="yt-dlp", available=True, path="yt-dlp"),
        ffmpeg=PreflightBinaryInfo(name="ffmpeg", available=True, path="ffmpeg"),
        ffprobe=PreflightBinaryInfo(name="ffprobe", available=True, path="ffprobe"),
    )

    stages_seen: list[tuple[str, float]] = []
    proofread_prompts: list[str] = []
    transcript_existed_before_analyzer = False

    def fake_post(url, headers=None, json=None, timeout=None):
        proofread_prompts.append((json or {})["messages"][1]["content"])
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": '{"corrections": [{"index": 1, "text": "Bawa tas 200 juta ke Sila."}]}'
                        }
                    }
                ]
            },
            text="",
        )

    monkeypatch.setattr(compat_mod.requests, "post", fake_post)

    orch = PipelineOrchestrator(
        settings_provider=lambda: settings,
        preflight_provider=lambda: preflight,
        on_state_change=lambda st: stages_seen.append((st.current_stage, st.progress_pct)),
    )

    async def fake_ingest(**kwargs):
        dl = DownloadResult(
            job_id=state.job_id,
            video_id="v1",
            title="Tas 200 Juta Ke Sila",
            duration_sec=120.0,
            width=1280,
            height=720,
            fps=30.0,
            source_video_path=str(src_mp4),
        )
        (job_dir / "metadata.json").write_text(dl.model_dump_json(indent=2), encoding="utf-8")
        return dl

    async def fake_transcribe(**kwargs):
        tr = TranscriptResult(
            job_id=state.job_id,
            source="faster_whisper",
            language="id",
            segments=[
                TranscriptSegment(index=0, start=0.0, end=5.0, text="Segmen awal di luar klip."),
                TranscriptSegment(index=1, start=40.0, end=45.0, text="Bawa tas 200 juta ke silah."),
                TranscriptSegment(index=2, start=90.0, end=95.0, text="Segmen akhir di luar klip."),
            ],
        )
        (job_dir / "transcript.json").write_text(tr.model_dump_json(indent=2), encoding="utf-8")
        return tr

    async def fake_analyze(**kwargs):
        nonlocal transcript_existed_before_analyzer
        transcript_existed_before_analyzer = (job_dir / "transcript.json").is_file()
        an = AnalysisResult(
            job_id=state.job_id,
            model="openai:gpt-4o-mini",
            clips=[
                ClipCandidate(
                    title="Bawa Tas 200 Juta!",
                    start_time=38.0,
                    end_time=68.0,
                    virality_score=9,
                    reason="Kocak banget.",
                )
            ],
        )
        (job_dir / "analysis.json").write_text(an.model_dump_json(indent=2), encoding="utf-8")
        return an

    async def fake_cut(**kwargs):
        tr_passed = kwargs["transcript"]
        assert tr_passed.segments[1].text == "Bawa tas 200 juta ke Sila."
        rr = RenderResult(
            job_id=state.job_id,
            aspect_ratio="9:16",
            framing_strategy="smart_face",
            padding_sec=0.4,
            output_dir=str(job_dir / "clips"),
            rendered_clips=[],
        )
        (job_dir / "render_result.json").write_text(rr.model_dump_json(indent=2), encoding="utf-8")
        return rr

    monkeypatch.setattr(orch._downloader, "ingest_local_file", fake_ingest)
    monkeypatch.setattr(orch._transcriber, "run", fake_transcribe)
    monkeypatch.setattr(orch._analyzer, "run", fake_analyze)
    monkeypatch.setattr(orch._cutter, "run", fake_cut)

    final_state = await orch.run_job(state, resume=False)
    assert final_state.status == JobStatus.COMPLETED
    assert transcript_existed_before_analyzer is True
    assert ("transcriber", 55.0) in stages_seen
    assert any(stage == "proofreader" for stage, _ in stages_seen)
    assert len(proofread_prompts) == 1
    assert "[1]" in proofread_prompts[0]
    assert "[0]" not in proofread_prompts[0]
    assert "[2]" not in proofread_prompts[0]
    persisted = TranscriptResult.model_validate_json((job_dir / "transcript.json").read_text(encoding="utf-8"))
    assert persisted.segments[1].text == "Bawa tas 200 juta ke Sila."


@pytest.mark.asyncio
async def test_whisper_speed_profiles_model_cache_and_cuda_precheck(tmp_path: Path, monkeypatch) -> None:
    import faster_whisper
    from atro.modules.transcriber import whisper_engine as we

    we.clear_whisper_model_cache()
    threads = we.get_optimal_cpu_threads()
    assert 1 <= threads <= 8

    init_calls: list[dict] = []
    transcribe_calls: list[dict] = []

    class FakeModel:
        def __init__(self, model_name, device="cpu", compute_type="int8", cpu_threads=0):
            init_calls.append({
                "model": model_name,
                "device": device,
                "compute_type": compute_type,
                "cpu_threads": cpu_threads,
            })

        def transcribe(
            self,
            audio,
            language=None,
            vad_filter=True,
            vad_parameters=None,
            word_timestamps=True,
            condition_on_previous_text=False,
            beam_size=5,
            best_of=5,
            temperature=None,
            initial_prompt=None,
            hotwords=None,
            repetition_penalty=1.08,
            no_repeat_ngram_size=3,
            hallucination_silence_threshold=1.5,
        ):
            transcribe_calls.append({
                "kind": "seq",
                "beam_size": beam_size,
                "best_of": best_of,
                "temperature": temperature,
            })
            seg = SimpleNamespace(
                start=0.1,
                end=1.4,
                text="Halo tes cepat.",
                words=[
                    SimpleNamespace(word="Halo", start=0.1, end=0.5),
                    SimpleNamespace(word="tes", start=0.55, end=0.9),
                    SimpleNamespace(word="cepat.", start=0.95, end=1.4),
                ],
            )
            return iter([seg]), SimpleNamespace(language="id")

    class FakeBatched:
        def __init__(self, model):
            self.model = model

        def transcribe(
            self,
            audio,
            language=None,
            vad_filter=True,
            vad_parameters=None,
            word_timestamps=True,
            condition_on_previous_text=False,
            beam_size=1,
            batch_size=8,
            temperature=None,
            initial_prompt=None,
            hotwords=None,
            repetition_penalty=1.08,
            no_repeat_ngram_size=3,
            hallucination_silence_threshold=1.5,
        ):
            transcribe_calls.append({
                "kind": "batched",
                "beam_size": beam_size,
                "batch_size": batch_size,
                "temperature": temperature,
                "hotwords": hotwords,
            })
            seg = SimpleNamespace(
                start=0.1,
                end=1.4,
                text="Halo batched cepat.",
                words=[
                    SimpleNamespace(word="Halo", start=0.1, end=0.5),
                    SimpleNamespace(word="batched", start=0.55, end=0.95),
                    SimpleNamespace(word="cepat.", start=1.0, end=1.4),
                ],
            )
            return iter([seg]), SimpleNamespace(language="id")

    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(faster_whisper, "BatchedInferencePipeline", FakeBatched)
    monkeypatch.setattr(we, "_probe_cuda_ready", lambda: (False, "cublas64_12.dll belum terpasang"))

    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    logger = JobLogger("j_sp", tmp_path / "job.log")

    # 1) Auto device skips broken CUDA completely and runs fast batched mode on CPU
    segs1, lang1, used1 = await we.transcribe_with_faster_whisper(
        audio_path=wav,
        video_duration_sec=5.0,
        model_name="small",
        device_pref="auto",
        logger=logger,
        language_hint="id",
        hotwords="Halo, Tes",
        speed_mode="fast",
    )
    assert lang1 == "id"
    assert used1 == "small (cpu/int8)"
    assert len(init_calls) == 1
    assert init_calls[0]["device"] == "cpu"
    assert init_calls[0]["cpu_threads"] == threads
    assert transcribe_calls[-1]["kind"] == "batched"
    assert transcribe_calls[-1]["beam_size"] == 1
    assert transcribe_calls[-1]["batch_size"] == 8
    assert transcribe_calls[-1]["hotwords"] is None
    assert "batched" in segs1[0].text

    # 2) Repeat call with same model/device/threads reuses cached WhisperModel (no duplicate load!)
    await we.transcribe_with_faster_whisper(
        audio_path=wav,
        video_duration_sec=5.0,
        model_name="small",
        device_pref="auto",
        logger=logger,
        language_hint="id",
        hotwords="Halo, Tes",
        speed_mode="balanced",
    )
    assert len(init_calls) == 1
    assert transcribe_calls[-1]["kind"] == "batched"
    assert transcribe_calls[-1]["beam_size"] == 2
    assert transcribe_calls[-1]["hotwords"] == "Halo, Tes"

    # 3) Accurate mode uses classic sequential beam=5 while still hitting model cache
    await we.transcribe_with_faster_whisper(
        audio_path=wav,
        video_duration_sec=5.0,
        model_name="small",
        device_pref="cpu",
        logger=logger,
        language_hint="id",
        speed_mode="accurate",
    )
    assert len(init_calls) == 1
    assert transcribe_calls[-1]["kind"] == "seq"
    assert transcribe_calls[-1]["beam_size"] == 5
    we.clear_whisper_model_cache()


@pytest.mark.asyncio
async def test_extract_audio_wav_skips_fresh_cached_wav_and_settings_speed_ui(tmp_path: Path, monkeypatch) -> None:
    import os
    from atro.config import load_settings
    from atro.modules.transcriber import whisper_engine as we
    from atro.ui.widgets.settings_dialog import SettingsDialog

    src = tmp_path / "source.mp4"
    src.write_bytes(b"video")
    wav = tmp_path / "audio.wav"
    wav.write_bytes(b"R" * 2048)
    os.utime(src, (1000, 1000))
    os.utime(wav, (2000, 2000))

    called = False

    async def fail_if_called(*_a, **_kw):
        nonlocal called
        called = True
        return 0, []

    monkeypatch.setattr(we, "run_subprocess_streaming", fail_if_called)
    out = await we.extract_audio_wav(src, wav, Path("ffmpeg"), JobLogger("j_ex", tmp_path / "log.txt"))
    assert out == wav
    assert called is False

    monkeypatch.setenv("ATRO_WHISPER_SPEED_MODE", "balanced")
    loaded = load_settings(base_dir=tmp_path)
    assert loaded.whisper_speed_mode == "balanced"

    ensure_qt_gui_app()
    dlg = SettingsDialog(AppSettings(whisper_speed_mode="fast"))
    try:
        assert dlg.whisper_speed_combo.currentData() == "fast"
        dlg.whisper_speed_combo.setCurrentIndex(dlg.whisper_speed_combo.findData("accurate"))
        dlg._on_save()
        assert dlg.updated_settings().whisper_speed_mode == "accurate"
    finally:
        dlg.close()
        dlg.deleteLater()
