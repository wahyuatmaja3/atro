"""Unit, real-FFmpeg, and Qt smoke tests for ATRO Modular Template Composer & Live Preview."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from atro.config import AppSettings
from atro.core.composer_schema import (
    ClipComposerConfig,
    CommentBubbleItem,
    TranscriptSegmentOverride,
)
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies
from atro.core.presets import (
    BUILTIN_PRESET_IDS,
    CAPTION_RECIPES,
    READY_TEMPLATES,
    apply_caption_recipe,
    build_preset_config,
    build_ready_template_config,
    generate_social_copywriting,
    load_custom_preset,
    load_or_create_job_clip_config,
    save_custom_preset,
    save_job_clip_config,
)
from atro.core.schemas import DownloadResult, TranscriptSegment, TranscriptWord
from atro.modules.cutter.composer_renderer import ensure_qt_gui_app, render_preview_frame
from atro.modules.cutter.service import CutterService, apply_initial_style_overrides
from atro.modules.cutter.subtitle_burner import (
    build_composer_caption_chunks,
    generate_composer_ass_file,
    hex_to_ass_color,
)
from atro.modules.transcriber.subtitle_parser import parse_vtt
from atro.modules.transcriber.whisper_engine import build_segments_from_whisper_words


def test_composer_schema_and_presets_mix_and_match(tmp_path: Path) -> None:
    for pid in BUILTIN_PRESET_IDS:
        cfg = build_preset_config(pid, clip_title=f"Judul {pid}", start_time=10.0, end_time=45.0, clip_index=2)
        assert cfg.preset_id == pid
        assert cfg.canvas_width == 1080
        assert cfg.canvas_height == 1920
        assert cfg.clip_index == 2

    # Mix-and-match: Podcast Hook Banner + Gaming Split + Twitch Comment Bubble + Subathon Angled Divider
    mixed = build_preset_config("gaming_split", clip_title="Mix Format Test", start_time=5.0, end_time=35.0)
    mixed.preset_id = "custom_mix"
    mixed.layout.divider.angle_deg = -7.0
    mixed.overlays.hook_banner.enabled = True
    mixed.overlays.hook_banner.transition_in = "whip"
    mixed.overlays.comment_bubbles.append(
        CommentBubbleItem(
            id="cb_mix",
            author_label="viewers:",
            username="pro_viewer",
            text="Kok bisa clutch segila itu bang?",
            start_sec=1.0,
            end_sec=4.5,
        )
    )
    mixed.sync_subtitle_anchor()
    assert mixed.subtitles.pos_y == 768  # 1920 * 0.40

    preset_file = tmp_path / "custom_mix.json"
    save_custom_preset(mixed, preset_file)
    reloaded = load_custom_preset(preset_file)
    assert reloaded.layout.divider.angle_deg == -7.0
    assert reloaded.overlays.hook_banner.transition_in == "whip"
    assert len(reloaded.overlays.comment_bubbles) == 1
    assert reloaded.overlays.comment_bubbles[0].username == "pro_viewer"

    job_dir = tmp_path / "job_1"
    saved_job_cfg = save_job_clip_config(job_dir, reloaded)
    assert saved_job_cfg.is_file()
    loaded_job_cfg = load_or_create_job_clip_config(
        job_dir=job_dir,
        clip_index=reloaded.clip_index,
        clip_title="Ignored",
        start_time=5.0,
        end_time=35.0,
    )
    assert loaded_job_cfg.preset_id == "custom_mix"


def test_composer_ass_generator_multi_speaker_and_action_tokens(tmp_path: Path) -> None:
    assert hex_to_ass_color("#FACC15") == "&H0015CCFA&"
    assert hex_to_ass_color("#80000000", trailing_amp=False) == "&H7F000000"

    cfg = build_preset_config("gaming_split", clip_title="Split Center Sub", start_time=10.0, end_time=18.0)
    cfg.timing.padding_sec = 0.0
    cfg.subtitles.multi_speaker_enabled = True
    cfg.subtitles.footnote.enabled = True
    cfg.subtitles.footnote.text = "Catatan: Momen Lucu"
    cfg.transcript_overrides = [
        TranscriptSegmentOverride(
            index=0,
            start=10.2,
            end=13.5,
            text="Wah gila sih ini *LAUGHS* banget!",
            speaker="SPEAKER_1",
        ),
        TranscriptSegmentOverride(
            index=1,
            start=13.6,
            end=17.5,
            text="Seriusan menang ronde terakhir?",
            speaker="SPEAKER_2",
        ),
    ]

    chunks = build_composer_caption_chunks(cfg, segments=[])
    assert len(chunks) >= 2
    assert chunks[0].speaker == "SPEAKER_1"
    assert any(c.speaker == "SPEAKER_2" for c in chunks)

    ass_out = generate_composer_ass_file(
        output_ass_path=tmp_path / "composer.ass",
        config=cfg,
        segments=[],
        clip_duration_sec=8.0,
    )
    assert ass_out is not None and ass_out.is_file()
    text = ass_out.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in text
    assert "PlayResY: 1920" in text
    assert "\\pos(540,768)" in text
    assert "*LAUGHS*" in text
    # Action token orange color &H001673F9&
    assert hex_to_ass_color(cfg.subtitles.action_token_color_hex) in text
    # SPEAKER_2 highlight cyan &H00F8BD38&
    assert hex_to_ass_color("#38BDF8") in text
    assert "Style: FootnoteSub" in text
    assert "Catatan: Momen Lucu" in text

    # Kinetic 1-2 word mode
    cfg_kin = build_preset_config("twitch_comment", start_time=10.0, end_time=18.0)
    cfg_kin.timing.padding_sec = 0.0
    cfg_kin.transcript_overrides = cfg.transcript_overrides
    kin_chunks = build_composer_caption_chunks(cfg_kin, segments=[])
    assert all(len(c.words) <= 2 for c in kin_chunks)


def test_render_preview_frame_all_layouts() -> None:
    ensure_qt_gui_app()
    src = np.zeros((720, 1280, 3), dtype=np.uint8)
    src[:, :640] = (180, 90, 40)
    src[:, 640:] = (40, 160, 120)

    segs = [
        TranscriptSegment(index=0, start=0.0, end=4.0, text="Coba lihat preview ini sekarang *LAUGHS*"),
    ]

    for pid in BUILTIN_PRESET_IDS:
        cfg = build_preset_config(pid, clip_title=f"Preview {pid}", start_time=0.0, end_time=10.0)
        cfg.timing.padding_sec = 0.0
        cfg.overlays.watermark.enabled = True
        img = render_preview_frame(src, cfg, rel_time_sec=1.5, segments=segs)
        assert not img.isNull()
        assert img.width() == 1080
        assert img.height() == 1920

    # Also explicitly test multi_speaker_split & blurred_frame 1:1
    cfg_ms = build_preset_config("subathon", start_time=0.0, end_time=10.0)
    cfg_ms.layout.mode = "multi_speaker_split"
    cfg_ms.layout.divider.angle_deg = 0.0
    img_ms = render_preview_frame(src, cfg_ms, rel_time_sec=1.0, segments=segs)
    assert img_ms.width() == 1080 and img_ms.height() == 1920

    cfg_bf = build_preset_config("twitch_comment", start_time=0.0, end_time=10.0)
    cfg_bf.layout.blurred_frame_aspect = "1:1"
    img_bf = render_preview_frame(src, cfg_bf, rel_time_sec=1.0, segments=segs)
    assert img_bf.width() == 1080 and img_bf.height() == 1920


@pytest.mark.asyncio
async def test_composer_real_ffmpeg_mix_and_match_render(tmp_path: Path) -> None:
    bgm_dir = tmp_path / "backsound"
    bgm_dir.mkdir(parents=True)
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=bgm_dir,
    )
    preflight = check_startup_dependencies(settings)
    assert preflight.ok and preflight.ffmpeg.path and preflight.ffprobe.path

    job_dir = settings.workspace_dir / "job_cmp"
    job_dir.mkdir(parents=True)
    source_mp4 = job_dir / "source.mp4"
    bgm_wav = bgm_dir / "synth_loop.wav"

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
        job_id="job_cmp",
        url="https://youtu.be/synthetic",
        video_id="synthetic",
        title="Mix Studio Test",
        channel="ATROTester",
        duration_sec=5.0,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
        source_video_path=str(source_mp4),
    )

    # Mix: Subathon angled split (-7 deg) + Podcast Whip Hook + Twitch Comment Bubble + Zoom Punch + Loudnorm/Ducking
    cfg = build_preset_config(
        "subathon",
        clip_title="Klip Mix Viral Banget!",
        start_time=0.5,
        end_time=4.5,
        clip_index=1,
        padding_sec=0.4,
        backsound_dir=str(bgm_dir),
    )
    cfg.layout.framing_tracking_mode = "manual_crop"
    cfg.layout.top_crop.auto_detect = False
    cfg.layout.bottom_crop.auto_detect = False
    cfg.layout.divider.angle_deg = -7.0
    cfg.overlays.hook_banner.enabled = True
    cfg.overlays.hook_banner.transition_in = "whip"
    cfg.overlays.hook_banner.duration_sec = 2.0
    cfg.overlays.comment_bubbles = [
        CommentBubbleItem(
            id="cb_1",
            enabled=True,
            author_label="viewers:",
            username="chat_id",
            text="Gila ini split miringnya keren banget!",
            start_sec=1.0,
            end_sec=3.8,
        )
    ]
    cfg.overlays.watermark.enabled = True
    cfg.overlays.watermark.text = "@ATROStudio"
    cfg.overlays.climax_effects.zoom_punch_enabled = True
    cfg.overlays.climax_effects.zoom_punch_start_sec = 2.0
    cfg.overlays.climax_effects.zoom_punch_duration_sec = 1.2
    cfg.audio.selection_mode = "manual"
    cfg.audio.selected_track_filename = "synth_loop.wav"
    cfg.audio.audio_ducking = True
    cfg.audio.vocal_loudnorm = True
    cfg.transcript_overrides = [
        TranscriptSegmentOverride(index=0, start=0.2, end=2.4, text="Ini pembuka gokil *LAUGHS*", speaker="SPEAKER_1"),
        TranscriptSegmentOverride(index=1, start=2.5, end=4.8, text="Langsung masuk ke klimaks!", speaker="SPEAKER_2"),
    ]

    logger = JobLogger("job_cmp", job_dir / "job.log")
    rendered = await CutterService().render_clip_with_composer_config(
        job_id="job_cmp",
        download=download,
        config=cfg,
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
        transcript_segments=[],
        settings=settings,
    )

    out_mp4 = Path(rendered.output_path)
    assert out_mp4.is_file() and out_mp4.stat().st_size > 0
    assert rendered.subtitles_burned is True
    assert rendered.backsound_used == "synth_loop.wav"
    assert rendered.composer_config_path is not None and Path(rendered.composer_config_path).is_file()

    # Temp composer assets in clips/ must be cleaned up
    clips_dir = job_dir / "clips"
    assert list(clips_dir.glob("_cmp_*")) == []

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
    assert abs(float(info["format"]["duration"]) - 4.8) <= 0.5


def test_clip_studio_widget_ui_smoke(tmp_path: Path) -> None:
    from atro.ui.widgets.clip_studio import ClipStudioWidget

    ensure_qt_gui_app()
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin", backsound_dir=tmp_path / "bgm")
    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        studio.apply_preset("gaming_split")
        assert studio.current_config().layout.mode == "split_screen"

        studio.apply_preset("twitch_comment")
        assert studio.current_config().layout.mode == "blurred_frame"
        assert len(studio.current_config().overlays.comment_bubbles) >= 1

        studio.apply_preset("subathon")
        assert studio.current_config().layout.divider.angle_deg == -7.0

        # Mutate controls programmatically and verify config updates immediately
        studio.split_ratio_slider.setValue(45)
        studio.divider_angle_slider.setValue(-12)
        studio.sub_upper_chk.setChecked(False)
        studio.sub_offset_spin.setValue(0.15)
        studio.zoom_punch_chk.setChecked(True)
        studio.timeline_slider.setValue(350)
        studio._set_preview_view_mode("preview")
        assert studio.preview_wrap.isHidden() is False
        assert studio.crop_wrap.isHidden() is True
        studio._set_preview_view_mode("both")

        cfg = studio.current_config()
        assert cfg.layout.split_ratio_top == pytest.approx(0.45, abs=0.01)
        assert cfg.layout.divider.angle_deg == -12.0
        assert cfg.subtitles.uppercase is False
        assert cfg.subtitles.timing_offset_sec == pytest.approx(0.15, abs=0.01)
        assert cfg.overlays.climax_effects.zoom_punch_enabled is True
    finally:
        studio.close()
        studio.deleteLater()


def test_word_level_timestamps_prevent_premature_subtitles() -> None:
    # 1. Whisper word splitting across silence gap >= 0.35s snaps start to first word onset
    raw_words = [
        (" Halo", 1.40, 1.70),
        (" guys", 1.72, 2.00),
        ("!", 2.00, 2.05),
        (" Ini", 3.10, 3.35),
        (" gila", 3.38, 3.70),
    ]
    segs = build_segments_from_whisper_words(
        raw_words=raw_words,
        fallback_start=0.50,  # premature VAD pad start
        fallback_end=3.90,
        fallback_text="Halo guys! Ini gila",
    )
    assert len(segs) == 2
    assert segs[0].start == pytest.approx(1.40, abs=0.01)
    assert segs[0].text == "Halo guys!"
    assert segs[1].start == pytest.approx(3.10, abs=0.01)
    assert segs[1].text == "Ini gila"

    # 2. WebVTT inline word timestamps extract exact word timing
    vtt = """WEBVTT

00:00:00.500 --> 00:00:04.000
<c>Satu</c><00:00:02.200><c> dua</c><00:00:02.800><c> tiga</c>
"""
    vtt_segs = parse_vtt(vtt)
    assert len(vtt_segs) == 1
    assert len(vtt_segs[0].words) == 3
    assert vtt_segs[0].words[1].start == pytest.approx(2.2, abs=0.01)
    assert vtt_segs[0].words[2].start == pytest.approx(2.8, abs=0.01)

    # 3. Composer caption chunking honors word timestamps, pause breaks (>= 0.22s), and timing_offset_sec
    cfg = build_preset_config("podcast", start_time=0.0, end_time=6.0)
    cfg.timing.padding_sec = 0.0
    cfg.subtitles.timing_offset_sec = 0.15

    seg_with_pause = TranscriptSegment(
        index=0,
        start=0.2,
        end=4.0,
        text="Tunggu dulu ini baru mulai",
        words=[
            TranscriptWord(word="Tunggu", start=1.00, end=1.30),
            TranscriptWord(word="dulu", start=1.32, end=1.60),
            # 0.50s pause before "ini"
            TranscriptWord(word="ini", start=2.10, end=2.35),
            TranscriptWord(word="baru", start=2.38, end=2.65),
            TranscriptWord(word="mulai", start=2.68, end=3.00),
        ],
    )
    chunks = build_composer_caption_chunks(cfg, segments=[seg_with_pause])
    assert len(chunks) == 2
    assert [w.text for w in chunks[0].words] == ["Tunggu", "dulu"]
    assert [w.text for w in chunks[1].words] == ["ini", "baru", "mulai"]
    # Chunk 1 must NOT appear before 1.00 + 0.15 = 1.15s; Chunk 2 must NOT appear before 2.10 + 0.15 = 2.25s
    assert chunks[0].start == pytest.approx(1.15, abs=0.01)
    assert chunks[1].start == pytest.approx(2.25, abs=0.01)


def test_initial_style_overrides_and_wizard_navigation(tmp_path: Path, monkeypatch) -> None:
    from atro.ui import main_window as mw_mod

    # 1. Verify apply_initial_style_overrides mutates ClipComposerConfig upfront
    cfg = build_preset_config("gaming_split", start_time=0.0, end_time=10.0)
    apply_initial_style_overrides(
        cfg,
        {
            "split_ratio_top": 0.45,
            "subtitle_highlight_hex": "#F97316",
            "subtitle_placement": "split_line_center",
            "zoom_punch_enabled": True,
        },
    )
    assert cfg.layout.split_ratio_top == pytest.approx(0.45, abs=0.01)
    assert cfg.subtitles.highlight_color_hex == "#F97316"
    assert cfg.subtitles.pos_y == int(round(1920 * 0.45))
    assert cfg.overlays.climax_effects.zoom_punch_enabled is True

    # 2. Verify 4-step WinUI Wizard navigation & pre-job style selection in MainWindow
    ensure_qt_gui_app()
    fake_settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    monkeypatch.setattr(mw_mod, "load_settings", lambda: fake_settings)

    win = mw_mod.MainWindow()
    try:
        assert win.wizard_stack.count() == 4
        assert win.wizard_stack.currentIndex() == 0

        # Navigate Step 1 -> Step 2
        win.go_to_step(1)
        assert win.wizard_stack.currentIndex() == 1

        # Select Subathon style card upfront and verify synced controls
        win._select_initial_style("subathon")
        assert win.format_combo.currentData() == "subathon"
        assert win.init_split_spin.value() == 50
        overrides = win._collect_initial_style_overrides("subathon")
        assert overrides["split_ratio_top"] == 0.5
        assert overrides["caption_recipe"] == "mrbeast_pill"
        assert overrides["active_word_bg_hex"] == "#F97316"
        assert overrides["subtitle_highlight_hex"] == "#FFFFFF"

        # Navigate to Step 3 and Step 4
        win.go_to_step(2)
        assert win.wizard_stack.currentIndex() == 2
        win.go_to_step(3)
        assert win.wizard_stack.currentIndex() == 3
    finally:
        win.close()
        win.deleteLater()


def test_eight_caption_recipes_and_social_copywriting() -> None:
    expected_ids = {
        "hormozi_viral",
        "mrbeast_pill",
        "lokaclip_neon",
        "mageran_glass",
        "abdaal_minimal",
        "gamer_split",
        "streamer_kinetic",
        "podcast_editorial",
    }
    assert set(CAPTION_RECIPES.keys()) == expected_ids

    cfg = build_preset_config("podcast", clip_title="Rahasia Bisnis Cuan", start_time=0.0, end_time=15.0)
    cfg.subtitles.pos_x = 512
    cfg.subtitles.pos_y = 1480
    cfg.subtitles.placement_preset = "custom"

    for rid in expected_ids:
        apply_caption_recipe(cfg, rid)
        assert cfg.subtitles.caption_recipe == rid
        # Anchors must never be overwritten when swapping caption recipe
        assert cfg.subtitles.pos_x == 512
        assert cfg.subtitles.pos_y == 1480
        assert cfg.subtitles.placement_preset == "custom"

    pack = generate_social_copywriting(
        clip_title="Cara Cuan 100 Juta Pertama",
        reason="Pembahasan konkret langkah praktis membangun bisnis.",
        preset_id="podcast",
    )
    assert pack["hook_headline"]
    assert "#FYP" in pack["hashtags"]
    assert "#PodcastClips" in pack["hashtags"]
    assert "#FYP" in pack["full_post"]


def test_pill_glass_dual_keyword_and_emoji_ass_and_preview(tmp_path: Path) -> None:
    ensure_qt_gui_app()
    src = np.full((720, 1280, 3), (45, 58, 72), dtype=np.uint8)
    segs = [
        TranscriptSegment(
            index=0,
            start=0.0,
            end=3.5,
            text="Cara gila dapat uang cepat",
            words=[
                TranscriptWord(word="Cara", start=0.1, end=0.6),
                TranscriptWord(word="gila", start=0.6, end=1.2),
                TranscriptWord(word="dapat", start=1.2, end=1.7),
                TranscriptWord(word="uang", start=1.7, end=2.4),
                TranscriptWord(word="cepat", start=2.4, end=3.3),
            ],
        )
    ]

    for recipe_id, expect_bg in (
        ("mrbeast_pill", "active_word_pill"),
        ("mageran_glass", "full_line_box"),
        ("hormozi_viral", "none"),
    ):
        cfg = build_preset_config("podcast", clip_title="Judul Viral", start_time=0.0, end_time=3.5)
        cfg.timing.padding_sec = 0.0
        apply_caption_recipe(cfg, recipe_id)
        cfg.subtitles.max_words_per_line = 5
        cfg.subtitles.dual_keyword_enabled = True
        cfg.subtitles.secondary_keyword_color_hex = "#22C55E"
        cfg.subtitles.auto_emoji_enabled = True
        cfg.overlays.climax_effects.vignette_enabled = True
        cfg.overlays.climax_effects.topic_header_enabled = True
        cfg.overlays.climax_effects.topic_header_text = "TOPIK HANGAT HARI INI"
        assert cfg.subtitles.caption_bg_mode == expect_bg

        ass_path = generate_composer_ass_file(
            output_ass_path=tmp_path / f"{recipe_id}.ass",
            config=cfg,
            segments=segs,
            clip_duration_sec=3.5,
            clip_start_sec=0.0,
            clip_end_sec=3.5,
        )
        assert ass_path is not None and ass_path.is_file()
        content = ass_path.read_text(encoding="utf-8")
        if expect_bg != "none":
            assert "\\p1}" in content
            assert "Dialogue: 1," in content
        assert "\\fnSegoe UI Emoji" in content
        assert "&H005EC522&" in content  # #22C55E dual keyword tag

        img = render_preview_frame(
            source_frame_bgr=src,
            config=cfg,
            rel_time_sec=1.9,
            segments=segs,
            clip_start_sec=0.0,
            clip_end_sec=3.5,
        )
        assert img.width() == 1080
        assert img.height() == 1920


@pytest.mark.asyncio
async def test_pip_facecam_and_rounded_blurred_frame_real_ffmpeg(tmp_path: Path) -> None:
    ws_dir = tmp_path / "ws"
    settings = AppSettings(
        workspace_dir=ws_dir,
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    preflight = check_startup_dependencies(settings)
    if not (preflight.ffmpeg.available and preflight.ffprobe.available):
        pytest.skip("FFmpeg/ffprobe required")

    job_dir = ws_dir / "jobs" / "job_pip"
    job_dir.mkdir(parents=True, exist_ok=True)
    source_mp4 = tmp_path / "pip_src.mp4"

    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=3.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3.5",
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

    download = DownloadResult(
        job_id="job_pip",
        url="https://youtu.be/pip_synth",
        video_id="pip_synth",
        title="PiP & Rounded Streamer Test",
        channel="ATROTester",
        duration_sec=3.5,
        width=1280,
        height=720,
        fps=30.0,
        has_audio=True,
        source_video_path=str(source_mp4),
    )
    logger = JobLogger("job_pip", job_dir / "job.log")

    # Case 1: Floating Circular PiP Facecam + Pill Captions + Topic Pill + Vignette
    cfg_pip = build_preset_config("podcast", clip_title="PiP Gaming Viral", start_time=0.2, end_time=3.0, clip_index=1, padding_sec=0.2)
    cfg_pip.layout.mode = "pip_facecam"
    cfg_pip.layout.pip_shape = "circle"
    cfg_pip.layout.pip_position = "top_right"
    cfg_pip.layout.frame_border_width_px = 6
    cfg_pip.layout.frame_border_color_hex = "#F97316"
    apply_caption_recipe(cfg_pip, "mrbeast_pill")
    cfg_pip.overlays.climax_effects.vignette_enabled = True
    cfg_pip.overlays.climax_effects.topic_header_enabled = True
    cfg_pip.overlays.climax_effects.topic_header_text = "CLUTCH PALING GILA"
    cfg_pip.transcript_overrides = [
        TranscriptSegmentOverride(index=0, start=0.2, end=2.8, text="Ini momen cuan gila!", speaker="SPEAKER_1")
    ]

    r1 = await CutterService().render_clip_with_composer_config(
        job_id="job_pip",
        download=download,
        config=cfg_pip,
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
        transcript_segments=[],
        settings=settings,
    )
    out1 = Path(r1.output_path)
    assert out1.is_file() and out1.stat().st_size > 0
    probe1 = subprocess.run(
        [preflight.ffprobe.path, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(out1)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe1.stdout.strip() == "1080x1920"

    # Case 2: Rounded Blurred Frame + Mageran Glass Box
    cfg_blur = build_preset_config("twitch_comment", clip_title="Rounded Streamer Frame", start_time=0.2, end_time=3.0, clip_index=2, padding_sec=0.2)
    cfg_blur.layout.frame_corner_radius_px = 36
    cfg_blur.layout.frame_border_width_px = 6
    cfg_blur.layout.frame_border_color_hex = "#A855F7"
    apply_caption_recipe(cfg_blur, "mageran_glass")
    cfg_blur.transcript_overrides = [
        TranscriptSegmentOverride(index=0, start=0.2, end=2.8, text="Komentar chat paling kocak", speaker="SPEAKER_1")
    ]

    r2 = await CutterService().render_clip_with_composer_config(
        job_id="job_pip",
        download=download,
        config=cfg_blur,
        job_dir=job_dir,
        preflight=preflight,
        logger=logger,
        transcript_segments=[],
        settings=settings,
    )
    out2 = Path(r2.output_path)
    assert out2.is_file() and out2.stat().st_size > 0
    assert not list((job_dir / "clips").glob("_cmp_*"))


def test_step2_live_mini_preview_and_upfront_pip_overrides(tmp_path: Path, monkeypatch) -> None:
    from atro.ui import main_window as mw_mod

    ensure_qt_gui_app()
    fake_settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    monkeypatch.setattr(mw_mod, "load_settings", lambda: fake_settings)

    win = mw_mod.MainWindow()
    try:
        win.go_to_step(1)
        assert win.step2_preview_canvas._rendered_qimg is not None
        assert win.step2_preview_canvas._rendered_qimg.width() == 1080
        assert win.step2_preview_canvas._rendered_qimg.height() == 1920

        pip_idx = win.init_layout_mode_combo.findData("pip_facecam")
        win.init_layout_mode_combo.setCurrentIndex(pip_idx)
        rec_idx = win.init_recipe_combo.findData("lokaclip_neon")
        win.init_recipe_combo.setCurrentIndex(rec_idx)
        win.init_topic_hdr_chk.setChecked(True)
        win.init_topic_hdr_edit.setText("TOPIK TEST")

        ov = win._collect_initial_style_overrides(win.format_combo.currentData() or "podcast")
        assert ov["layout_mode"] == "pip_facecam"
        assert ov["caption_recipe"] == "lokaclip_neon"
        assert ov["topic_header_enabled"] is True
        assert ov["topic_header_text"] == "TOPIK TEST"
        assert "pip_facecam" in win.step2_preview_badge.text()

        # Select an all-in-one ready template card directly in Step 2
        win._select_initial_style("pip_facecam_bulat")
        ov2 = win._collect_initial_style_overrides(win.format_combo.currentData() or "game")
        assert ov2["ready_template_id"] == "pip_facecam_bulat"
        assert "pip_facecam" in win.step2_preview_badge.text()
        applied = build_preset_config("game", start_time=0.0, end_time=10.0)
        apply_initial_style_overrides(applied, ov2)
        assert applied.layout.mode == "pip_facecam"
        assert applied.layout.pip_shape == "circle"
        assert applied.subtitles.caption_recipe == "mrbeast_pill"
    finally:
        win.close()
        win.deleteLater()


def test_studio_template_tab_one_click_ready_templates(tmp_path: Path) -> None:
    from atro.ui.widgets.clip_studio import ClipStudioWidget

    ensure_qt_gui_app()
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        assert studio.ctrl_tabs.count() == 6
        expected_tabs = [
            "1. Template",
            "2. Tata Letak",
            "3. Teks Subtitle",
            "4. Judul & Komentar",
            "5. Musik & Suara",
            "6. Edit Kata",
        ]
        assert [studio.ctrl_tabs.tabText(i) for i in range(6)] == expected_tabs
        assert studio.ctrl_tabs.currentIndex() == 0
        assert set(studio.template_cards.keys()) == set(READY_TEMPLATES.keys())

        for tid, meta in READY_TEMPLATES.items():
            studio.template_cards[tid].clicked.emit(tid)
            cfg = studio.current_config()
            assert cfg.preset_id == tid
            assert cfg.layout.mode == meta["layout_mode"]
            assert cfg.subtitles.caption_recipe == meta["caption_recipe"]
            assert cfg.subtitles.placement_preset == meta["placement_preset"]
            assert studio.template_cards[tid].property("selected") is True
            assert studio.preview_canvas._rendered_qimg is not None

        # Verify direct builder matches schema and dimensions
        built = build_ready_template_config("podcast_mageran_glass", clip_title="Glass Sample")
        assert built.layout.mode == "blurred_frame"
        assert built.layout.blurred_frame_aspect == "16:9"
        assert built.subtitles.caption_bg_mode == "full_line_box"
        assert built.overlays.climax_effects.topic_header_enabled is True
    finally:
        studio.close()
        studio.deleteLater()


def test_hook_banner_multiline_and_font_size(tmp_path: Path) -> None:
    from atro.modules.cutter.composer_renderer import (
        _hook_card_dimensions,
        render_hook_banner_card_image,
    )
    from atro.modules.cutter.subtitle_burner import _wrap_hook_title
    from atro.ui.widgets.clip_studio import ClipStudioWidget

    ensure_qt_gui_app()
    cfg = build_preset_config("podcast", clip_title="Judul Standar")
    assert cfg.overlays.hook_banner.font_size == 34

    cfg.overlays.hook_banner.text = "Baris Satu\nBaris Dua\nBaris Tiga"
    card_w, card_h, lines, _font = _hook_card_dimensions(cfg.overlays.hook_banner, canvas_w=1080)
    assert lines == ["Baris Satu", "Baris Dua", "Baris Tiga"]
    assert 280 <= card_w <= 1000
    assert card_h >= 120

    hook_card = render_hook_banner_card_image(cfg.overlays.hook_banner, canvas_w=1080)
    assert hook_card is not None and hook_card[0].height() == card_h
    assert _wrap_hook_title("Baris Satu\nBaris Dua\nBaris Tiga") == "Baris Satu\\NBaris Dua\\NBaris Tiga"

    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        studio.hook_text_edit.setPlainText("Hook Baris 1\nHook Baris 2")
        studio.hook_size_spin.setValue(28)
        updated = studio.current_config()
        assert updated.overlays.hook_banner.text == "Hook Baris 1\nHook Baris 2"
        assert updated.overlays.hook_banner.font_size == 28
    finally:
        studio.close()
        studio.deleteLater()


@pytest.mark.asyncio
async def test_podcast_multi_angle_face_detection_and_ffmpeg_render(
    tmp_path: Path, monkeypatch
) -> None:
    import cv2
    from atro.modules.cutter import composer_renderer as cr_mod
    from atro.modules.cutter.framing import face_tracking as ft_mod

    ws_dir = tmp_path / "ws"
    settings = AppSettings(
        workspace_dir=ws_dir,
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    preflight = check_startup_dependencies(settings)
    if not (preflight.ffmpeg.available and preflight.ffprobe.available):
        pytest.skip("FFmpeg/ffprobe required")

    # Same studio background color (#283038) across BOTH camera angles:
    # Angle 1 (0..2s): bright GREEN left marker & speaker box; Angle 2 (2..4s): bright ORANGE-RED right box
    src_mp4 = tmp_path / "podcast_2cam.mp4"
    writer = cv2.VideoWriter(
        str(src_mp4),
        cv2.VideoWriter_fourcc(*"mp4v"),
        25.0,
        (1280, 720),
    )
    assert writer.isOpened()
    try:
        for i in range(100):  # 4.0 seconds @ 25 FPS
            frame = np.full((720, 1280, 3), (40, 48, 56), dtype=np.uint8)
            if i < 50:
                # Camera Angle 1: subject on LEFT side
                cv2.rectangle(frame, (120, 120), (420, 620), (35, 210, 60), -1)
            else:
                # Camera Angle 2: subject on RIGHT side
                cv2.rectangle(frame, (860, 120), (1160, 620), (40, 70, 235), -1)
            writer.write(frame)
    finally:
        writer.release()

    # Deterministic face detector reacting directly to the pixels of each camera angle
    def _pixel_face_detector(small_bgr, _gray, _y, _f, _p, **_kwargs):
        _sh, sw = small_bgr.shape[:2]
        left_green = float(np.mean(small_bgr[:, : sw // 2, 1]))
        right_red = float(np.mean(small_bgr[:, sw // 2 :, 2]))
        if left_green >= right_red:
            return [ft_mod.FaceBox(x=0.14, y=0.22, w=0.14, h=0.24, conf=0.95)]
        return [ft_mod.FaceBox(x=0.72, y=0.22, w=0.14, h=0.24, conf=0.95)]

    monkeypatch.setattr(ft_mod, "_detect_faces_in_frame", _pixel_face_detector)

    cfg = build_preset_config(
        "podcast",
        clip_title="Podcast Dual Kamera",
        start_time=0.0,
        end_time=4.0,
        clip_index=1,
        padding_sec=0.0,
    )
    segs = cr_mod.apply_auto_detect_to_layout_sync(
        source_path=src_mp4,
        start_sec=0.0,
        end_sec=4.0,
        layout=cfg.layout,
        src_w=1280,
        src_h=720,
        fps=25.0,
    )
    assert len(segs) >= 2
    assert cfg.layout.shot_crops[0].top_crop.x < 0.25
    assert cfg.layout.shot_crops[-1].top_crop.x > 0.55

    # Verify Live Preview switches crop horizontally as playhead crosses camera cut at t=2.0s
    f_left = np.full((720, 1280, 3), (40, 48, 56), dtype=np.uint8)
    cv2.rectangle(f_left, (120, 120), (420, 620), (35, 210, 60), -1)
    f_right = np.full((720, 1280, 3), (40, 48, 56), dtype=np.uint8)
    cv2.rectangle(f_right, (860, 120), (1160, 620), (40, 70, 235), -1)

    prev_0 = render_preview_frame(f_left, cfg, rel_time_sec=0.6, segments=[], clip_start_sec=0.0, clip_end_sec=4.0)
    prev_1 = render_preview_frame(f_right, cfg, rel_time_sec=3.0, segments=[], clip_start_sec=0.0, clip_end_sec=4.0)
    c_left = prev_0.pixelColor(540, 960)
    c_right = prev_1.pixelColor(540, 960)
    assert c_left.green() > c_left.red() + 40
    assert c_right.red() > c_right.green() + 40

    # Verify FFmpeg Filtergraph Compiler + real CutterService render
    plan = cr_mod.compile_composer_ffmpeg_plan(
        config=cfg,
        src_w=1280,
        src_h=720,
        fps=25.0,
        duration_sec=4.0,
        work_dir=tmp_path / "work_plan",
        segments=[],
    )
    joined_plan = "\n".join(plan.filter_lines)
    assert "concat=n=" in joined_plan
    assert "angles)" in plan.summary

    job_dir = ws_dir / "jobs" / "job_2cam"
    job_dir.mkdir(parents=True, exist_ok=True)
    # Mux silent AAC audio so CutterService can process source cleanly
    src_av = tmp_path / "podcast_2cam_av.mp4"
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-i",
            str(src_mp4),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4.0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(src_av),
        ],
        check=True,
        capture_output=True,
    )
    dl = DownloadResult(
        job_id="job_2cam",
        url="https://youtu.be/2cam",
        video_id="2cam",
        title="Dual Cam Podcast",
        channel="ATROTester",
        duration_sec=4.0,
        width=1280,
        height=720,
        fps=25.0,
        has_audio=True,
        source_video_path=str(src_av),
    )
    cfg.transcript_overrides = [
        TranscriptSegmentOverride(index=0, start=0.2, end=3.8, text="Dua kamera ganti angle", speaker="SPEAKER_1")
    ]
    rendered = await CutterService().render_clip_with_composer_config(
        job_id="job_2cam",
        download=dl,
        config=cfg,
        job_dir=job_dir,
        preflight=preflight,
        logger=JobLogger("job_2cam", job_dir / "job.log"),
        transcript_segments=[],
        settings=settings,
    )
    out_mp4 = Path(rendered.output_path)
    assert out_mp4.is_file() and out_mp4.stat().st_size > 0
    cap_out = cv2.VideoCapture(str(out_mp4))
    try:
        cap_out.set(cv2.CAP_PROP_POS_MSEC, 800.0)
        ok_a, frame_a = cap_out.read()
        cap_out.set(cv2.CAP_PROP_POS_MSEC, 3000.0)
        ok_b, frame_b = cap_out.read()
        assert ok_a and frame_a is not None
        assert ok_b and frame_b is not None
        # At 0.8s (green angle 1) center area should be predominantly green; at 3.0s (red/orange angle 2) predominantly red
        roi_a = frame_a[700:1100, 340:740]
        roi_b = frame_b[700:1100, 340:740]
        assert float(np.mean(roi_a[:, :, 1])) > float(np.mean(roi_a[:, :, 2])) + 30
        assert float(np.mean(roi_b[:, :, 2])) > float(np.mean(roi_b[:, :, 1])) + 30
    finally:
        cap_out.release()


def test_active_speaker_selection_in_two_person_shot(tmp_path: Path, monkeypatch) -> None:
    import cv2
    from atro.modules.cutter import composer_renderer as cr_mod
    from atro.modules.cutter.framing import face_tracking as ft_mod

    src_mp4 = tmp_path / "two_speakers_turns.mp4"
    writer = cv2.VideoWriter(
        str(src_mp4),
        cv2.VideoWriter_fourcc(*"mp4v"),
        25.0,
        (1280, 720),
    )
    assert writer.isOpened()
    try:
        for i in range(200):  # 8.0 seconds @ 25 FPS
            frame = np.full((720, 1280, 3), (38, 44, 54), dtype=np.uint8)
            # Stationary heads & eyes for both speakers
            cv2.rectangle(frame, (179, 158), (384, 374), (160, 178, 204), -1)  # LEFT face
            cv2.rectangle(frame, (220, 200), (340, 232), (45, 45, 45), -1)     # LEFT eyes
            cv2.rectangle(frame, (896, 158), (1101, 374), (160, 178, 204), -1) # RIGHT face
            cv2.rectangle(frame, (940, 200), (1060, 232), (45, 45, 45), -1)    # RIGHT eyes

            mouth_open = (i % 4) < 2
            if i < 100:
                # 0.0..4.0s: LEFT is silent (static mouth), RIGHT speaks rapidly
                cv2.rectangle(frame, (240, 306), (320, 322), (60, 50, 50), -1)
                if mouth_open:
                    cv2.rectangle(frame, (946, 290), (1052, 344), (245, 245, 245), -1)
                else:
                    cv2.rectangle(frame, (946, 306), (1052, 322), (30, 30, 30), -1)
            else:
                # 4.0..8.0s: RIGHT is silent (static mouth), LEFT speaks rapidly
                cv2.rectangle(frame, (955, 306), (1045, 322), (60, 50, 50), -1)
                if mouth_open:
                    cv2.rectangle(frame, (230, 290), (336, 344), (245, 245, 245), -1)
                else:
                    cv2.rectangle(frame, (230, 306), (336, 322), (30, 30, 30), -1)
            writer.write(frame)
    finally:
        writer.release()

    # Fixed 2-person wide shot returning both faces so real mouth activity decides who speaks
    def _two_face_detector(_small, _gray, _y, _f, _p, **_kwargs):
        return [
            ft_mod.FaceBox(x=0.14, y=0.22, w=0.16, h=0.30, conf=0.95),
            ft_mod.FaceBox(x=0.70, y=0.22, w=0.16, h=0.30, conf=0.95),
        ]

    monkeypatch.setattr(ft_mod, "_detect_faces_in_frame", _two_face_detector)

    cfg = build_preset_config(
        "podcast",
        clip_title="Debat Dua Pembicara",
        start_time=0.0,
        end_time=8.0,
        clip_index=1,
        padding_sec=0.0,
    )
    segs = cr_mod.apply_auto_detect_to_layout_sync(
        source_path=src_mp4,
        start_sec=0.0,
        end_sec=8.0,
        layout=cfg.layout,
        src_w=1280,
        src_h=720,
        fps=25.0,
    )
    assert len(segs) >= 2
    # Segment 1 (0..4s) must track the RIGHT active speaker, Segment 2 (4..8s) must switch to LEFT speaker
    assert segs[0].top_crop.x > 0.45
    assert segs[-1].top_crop.x < 0.30
    assert segs[0].top_crop.label.startswith("Speaker #")


@pytest.mark.asyncio
async def test_batch_apply_style_and_batch_render_multiple_clips(tmp_path: Path) -> None:
    from atro.core.presets import load_or_create_job_clip_config
    from atro.core.schemas import (
        AnalysisResult,
        ClipCandidate,
        JobArtifacts,
        JobRequest,
        JobState,
        JobStatus,
        RenderResult,
        RenderedClip,
        TranscriptResult,
    )
    from atro.pipeline.state_store import create_job_state, save_state
    from atro.ui.widgets.clip_studio import ClipStudioWidget

    ensure_qt_gui_app()
    ws_dir = tmp_path / "ws"
    job_id = "job_batch_01"
    job_dir = ws_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(
        workspace_dir=ws_dir,
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    preflight = check_startup_dependencies(settings)
    if not (preflight.ffmpeg.available and preflight.ffprobe.available):
        pytest.skip("FFmpeg/ffprobe required")

    source_mp4 = job_dir / "source.mp4"
    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=6:size=1280x720:rate=25",
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

    dl = DownloadResult(
        job_id=job_id,
        url="https://youtu.be/batch",
        video_id="batch",
        title="Podcast Batch Test",
        channel="ATROTester",
        duration_sec=6.0,
        width=1280,
        height=720,
        fps=25.0,
        has_audio=True,
        source_video_path=str(source_mp4),
    )
    (job_dir / "metadata.json").write_text(dl.model_dump_json(indent=2), encoding="utf-8")

    tr = TranscriptResult(
        job_id=job_id,
        source="faster_whisper",
        language="id",
        segments=[
            TranscriptSegment(index=0, start=0.2, end=1.8, text="Klip satu membahas strategi awal"),
            TranscriptSegment(index=1, start=2.2, end=3.8, text="Klip dua membahas eksekusi cepat"),
            TranscriptSegment(index=2, start=4.2, end=5.8, text="Klip tiga membahas hasil akhir"),
        ],
    )
    (job_dir / "transcript.json").write_text(tr.model_dump_json(indent=2), encoding="utf-8")

    analysis = AnalysisResult(
        job_id=job_id,
        model="gemini-4-neon",
        clips=[
            ClipCandidate(title="Judul Klip Satu", start_time=0.0, end_time=2.0, virality_score=9, reason="Menarik"),
            ClipCandidate(title="Judul Klip Dua", start_time=2.0, end_time=4.0, virality_score=8, reason="Jelas"),
            ClipCandidate(title="Judul Klip Tiga", start_time=4.0, end_time=6.0, virality_score=8, reason="Kuat"),
        ],
    )
    (job_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    req = JobRequest(source_type="local_file", local_video_path=str(source_mp4), clip_count=3)
    state = create_job_state(ws_dir, req, job_id=job_id)
    state.status = JobStatus.COMPLETED
    state.current_stage = "completed"
    state.progress_pct = 100.0
    state.artifacts = JobArtifacts(
        metadata_json=str(job_dir / "metadata.json"),
        transcript_json=str(job_dir / "transcript.json"),
        analysis_json=str(job_dir / "analysis.json"),
    )
    save_state(state)

    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        studio.load_job_context(state, select_clip_index=1)
        studio.apply_preset("gaming_split")
        studio.hook_size_spin.setValue(26)
        studio.sub_upper_chk.setChecked(False)
        studio.sub_size_spin.setValue(58)
        updated_count = studio.apply_current_style_to_clips([1, 2, 3], reset_auto_face=True)
        assert updated_count == 3

        # Verify each saved JSON inherited new template + hook size while preserving own title & window
        expected_windows = [(0.0, 2.0, "Judul Klip Satu"), (2.0, 4.0, "Judul Klip Dua"), (4.0, 6.0, "Judul Klip Tiga")]
        for idx, (st, en, title) in enumerate(expected_windows, start=1):
            loaded_cfg = load_or_create_job_clip_config(
                job_dir=job_dir,
                clip_index=idx,
                clip_title=title,
                start_time=st,
                end_time=en,
            )
            assert loaded_cfg.clip_title == title
            assert loaded_cfg.timing.start_time == pytest.approx(st, abs=0.01)
            assert loaded_cfg.timing.end_time == pytest.approx(en, abs=0.01)
            assert loaded_cfg.layout.mode == "split_screen"
            assert loaded_cfg.overlays.hook_banner.font_size == 26
            assert loaded_cfg.overlays.hook_banner.text == title
            assert loaded_cfg.subtitles.uppercase is False
            assert loaded_cfg.subtitles.font_size == 58

        # Disable TTS for fast offline test rendering, then batch-render clips [1, 2, 3]
        batch_style = studio.current_config()
        batch_style.timing.opening_tts_enabled = False
        batch_style.overlays.climax_effects.progress_bar_enabled = False
    finally:
        studio.close()
        studio.deleteLater()

    prog_events: list[tuple[int, int, str]] = []
    rendered_list = await CutterService().render_batch_clips_with_composer(
        job_id=job_id,
        download=dl,
        analysis=analysis,
        job_dir=job_dir,
        preflight=preflight,
        logger=JobLogger(job_id, job_dir / "job.log"),
        clip_indices=[1, 2, 3],
        transcript_segments=tr.segments,
        settings=settings,
        style_template_config=batch_style,
        force_redetect_faces=True,
        on_clip_progress=lambda d, t, ttl: prog_events.append((d, t, ttl)),
        request=req,
    )
    assert len(rendered_list) == 3
    assert [p[:2] for p in prog_events] == [(1, 3), (2, 3), (3, 3)]
    for item in rendered_list:
        assert Path(item.output_path).is_file()
        assert Path(item.output_path).stat().st_size > 0
        assert item.subtitles_burned is True

    rr = RenderResult.model_validate_json((job_dir / "render_result.json").read_text(encoding="utf-8"))
    assert len(rr.rendered_clips) == 3




