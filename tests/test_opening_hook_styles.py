"""Tests for viral multi-layer Hook Caption designs, 13-style gallery, intro modes, and TTS caption choice."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import get_args

import numpy as np
import pytest

from atro.config import AppSettings
from atro.core.composer_schema import HookBannerConfig, HookDesignStyle
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies
from atro.core.presets import (
    HOOK_STYLE_RECIPES,
    apply_hook_style_recipe,
    build_preset_config,
    build_ready_template_config,
)
from atro.core.schemas import DownloadResult, TranscriptSegment, TranscriptWord
from atro.modules.cutter import service as cutter_service_mod
from atro.modules.cutter.composer_renderer import (
    compile_composer_ffmpeg_plan,
    render_hook_banner_card_image,
    render_hook_style_thumbnail_qimage,
    render_preview_frame,
)
from atro.modules.cutter.service import CutterService, apply_initial_style_overrides
from tests.test_composer_studio import ensure_qt_gui_app


def _probe_duration(ffprobe_path: str, video_path: Path) -> float:
    res = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(res.stdout.strip())


def test_thirteen_hook_designs_schema_and_vector_render() -> None:
    ensure_qt_gui_app()
    schema_styles = set(get_args(HookDesignStyle))
    assert len(HOOK_STYLE_RECIPES) == 13
    assert schema_styles == set(HOOK_STYLE_RECIPES.keys())

    cfg = build_ready_template_config("ginux_drama_banner", clip_title="KETIKA RADITYA\nDIKA IKUT KESEL\nKE SILA")
    assert cfg.overlays.hook_banner.design_style == "viral_sticker"
    assert cfg.timing.opening_tts_enabled is True
    assert cfg.overlays.hook_banner.opening_intro_mode == "freeze_clear"

    for style_id in schema_styles:
        cfg_s = build_preset_config("podcast", clip_title="KETIKA RADITYA\nDIKA IKUT KESEL")
        cfg_s.overlays.hook_banner.enabled = True
        apply_hook_style_recipe(cfg_s, style_id)
        assert cfg_s.overlays.hook_banner.design_style == style_id
        assert cfg_s.overlays.hook_banner.text == "KETIKA RADITYA\nDIKA IKUT KESEL"

        rendered = render_hook_banner_card_image(cfg_s.overlays.hook_banner, canvas_w=1080)
        assert rendered is not None
        img, tx, ty = rendered
        assert 100 <= img.width() <= 1080
        assert img.height() >= 60
        assert 0 <= tx <= 1080

        thumb = render_hook_style_thumbnail_qimage(style_id, width=216, height=86)
        assert thumb.width() == 216 and thumb.height() == 86


def test_hook_intro_modes_and_zoom_frame0_visibility(tmp_path: Path) -> None:
    ensure_qt_gui_app()
    segs = [
        TranscriptSegment(
            index=0,
            start=0.0,
            end=3.0,
            text="Cerita lucu banget",
            words=[TranscriptWord(word="Cerita", start=0.2, end=0.8)],
        )
    ]

    for mode in ("freeze_clear", "freeze_zoom", "freeze_blur"):
        cfg = build_preset_config("podcast", clip_title="KETIKA LUCU!", start_time=0.0, end_time=6.0)
        cfg.timing.padding_sec = 0.0
        apply_hook_style_recipe(cfg, "viral_sticker")
        cfg.overlays.hook_banner.opening_intro_mode = mode
        cfg.overlays.hook_banner.match_opening_tts_duration = True

        plan = compile_composer_ffmpeg_plan(
            config=cfg,
            src_w=1280,
            src_h=720,
            fps=30.0,
            duration_sec=6.0,
            work_dir=tmp_path / f"plan_{mode}",
            segments=segs,
            opening_duration_sec=2.1,
            clip_start_sec=0.0,
            clip_end_sec=6.0,
        )
        joined = "\n".join(plan.filter_lines)
        assert "enable='between(t,0,2.100)'" in joined
        if mode == "freeze_blur":
            assert "boxblur=10:2" in joined
        else:
            assert "boxblur=10:2" not in joined
        if mode == "freeze_zoom":
            assert "1+0.07*min(1,t/2.100)" in joined

    # Frame 0 preview must visibly render viral_sticker text (non-empty overlay delta vs disabled)
    src = np.full((360, 640, 3), (40, 50, 60), dtype=np.uint8)
    cfg_on = build_ready_template_config("ginux_drama_banner", clip_title="KETIKA VIRAL!", start_time=0.0, end_time=6.0)
    cfg_on.overlays.hook_banner.enabled = True
    img_on = render_preview_frame(src, cfg_on, rel_time_sec=0.0, segments=segs, clip_start_sec=0.0, clip_end_sec=6.0)
    cfg_off = cfg_on.model_copy(deep=True)
    cfg_off.overlays.hook_banner.enabled = False
    img_off = render_preview_frame(src, cfg_off, rel_time_sec=0.0, segments=segs, clip_start_sec=0.0, clip_end_sec=6.0)
    assert img_on.bits().asstring(img_on.sizeInBytes()) != img_off.bits().asstring(img_off.sizeInBytes())


@pytest.mark.asyncio
async def test_opening_tts_uses_edited_hook_text_or_can_be_disabled_real_ffmpeg(
    tmp_path: Path, monkeypatch
) -> None:
    settings = AppSettings(
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
        backsound_dir=tmp_path / "bgm",
    )
    preflight = check_startup_dependencies(settings)
    assert preflight.ok and preflight.ffmpeg.path and preflight.ffprobe.path

    job_dir = settings.workspace_dir / "job_hook_tts"
    job_dir.mkdir(parents=True)
    source_mp4 = job_dir / "source.mp4"
    tts_fixture = tmp_path / "tts.wav"

    subprocess.run(
        [
            preflight.ffmpeg.path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=4:size=640x360:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
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
            "sine=frequency=520:duration=1.8",
            str(tts_fixture),
        ],
        check=True,
        capture_output=True,
    )

    captured_texts: list[str] = []

    async def fake_tts(text, output_audio_path, voice, rate, ffprobe_path, logger):
        captured_texts.append(text)
        dest = output_audio_path.with_suffix(".wav")
        dest.write_bytes(tts_fixture.read_bytes())
        return dest, 1.8

    monkeypatch.setattr(cutter_service_mod, "synthesize_opening_voiceover", fake_tts)

    download = DownloadResult(
        job_id="job_hook_tts",
        video_id="hooktts",
        title="Test Source",
        source_video_path=str(source_mp4),
        duration_sec=4.0,
        width=640,
        height=360,
        fps=24.0,
        has_audio=True,
    )
    segs = [TranscriptSegment(index=0, start=0.0, end=3.5, text="Ini klip utama mulai sekarang.")]

    # 1. With opening voice enabled -> reads edited hook_banner.text BEFORE clip starts
    cfg_tts = build_ready_template_config("ginux_drama_banner", clip_title="Judul Asli Clip", start_time=0.0, end_time=3.5)
    cfg_tts.timing.padding_sec = 0.0
    cfg_tts.timing.silence_trim_enabled = False
    cfg_tts.timing.opening_tts_enabled = True
    cfg_tts.overlays.hook_banner.enabled = True
    cfg_tts.overlays.hook_banner.text = "KETIKA RADITYA\nDIKA IKUT KESEL"
    cfg_tts.overlays.hook_banner.design_style = "viral_sticker"
    cfg_tts.overlays.hook_banner.opening_intro_mode = "freeze_clear"

    res_tts = await CutterService().render_clip_with_composer_config(
        job_id="job_hook_tts",
        download=download,
        config=cfg_tts,
        job_dir=job_dir,
        preflight=preflight,
        logger=JobLogger("job_hook_tts", job_dir / "log.txt"),
        transcript_segments=segs,
        settings=settings,
    )
    assert captured_texts == ["KETIKA RADITYA\nDIKA IKUT KESEL"]
    assert res_tts.opening_tts_used is True
    dur_with_tts = _probe_duration(preflight.ffprobe.path, Path(res_tts.output_path))
    assert dur_with_tts >= 5.0  # 3.5s clip + 1.8s pre-clip TTS intro

    # 2. With opening voice disabled -> NO TTS synthesized, caption overlays without intro delay
    captured_texts.clear()
    cfg_silent = cfg_tts.model_copy(deep=True)
    cfg_silent.clip_index = 2
    cfg_silent.timing.opening_tts_enabled = False
    res_silent = await CutterService().render_clip_with_composer_config(
        job_id="job_hook_tts",
        download=download,
        config=cfg_silent,
        job_dir=job_dir,
        preflight=preflight,
        logger=JobLogger("job_hook_tts", job_dir / "log2.txt"),
        transcript_segments=segs,
        settings=settings,
    )
    assert captured_texts == []
    assert res_silent.opening_tts_used is False
    dur_no_tts = _probe_duration(preflight.ffprobe.path, Path(res_silent.output_path))
    assert 3.2 <= dur_no_tts <= 4.0


def test_studio_tab4_and_step2_hook_design_and_voice_controls(tmp_path: Path, monkeypatch) -> None:
    from atro.ui import main_window as mw_mod
    from atro.ui.widgets.clip_studio import ClipStudioWidget

    ensure_qt_gui_app()
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin", backsound_dir=tmp_path / "bgm")
    monkeypatch.setattr(mw_mod, "load_settings", lambda: settings)

    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        assert studio.hook_design_combo.count() == len(HOOK_STYLE_RECIPES)
        assert len(studio._hook_design_cards) == len(HOOK_STYLE_RECIPES)

        # Select visual sticker design card and verify config updates
        studio._select_hook_design("viral_white_red")
        cfg = studio.current_config()
        assert cfg.overlays.hook_banner.design_style == "viral_white_red"
        assert studio.hook_shadow_btn.color_hex() == "#DC2626"

        # Verify explicit Tab 4 voice choice stays 2-way synced with Tab 5 opening_tts_chk
        idx_on = studio.hook_tts_mode_combo.findData(True)
        idx_off = studio.hook_tts_mode_combo.findData(False)
        assert idx_on >= 0 and idx_off >= 0

        studio.hook_tts_mode_combo.setCurrentIndex(idx_on)
        assert studio.opening_tts_chk.isChecked() is True
        assert studio.current_config().timing.opening_tts_enabled is True

        studio.hook_tts_mode_combo.setCurrentIndex(idx_off)
        assert studio.opening_tts_chk.isChecked() is False
        assert studio.current_config().timing.opening_tts_enabled is False

        # Flipping Tab 5 checkbox also flips Tab 4 voice combo
        studio.opening_tts_chk.setChecked(True)
        assert studio.hook_tts_mode_combo.currentData() is True

        # Switch intro frame mode
        im_zoom = studio.hook_intro_mode_combo.findData("freeze_zoom")
        studio.hook_intro_mode_combo.setCurrentIndex(im_zoom)
        assert studio.current_config().overlays.hook_banner.opening_intro_mode == "freeze_zoom"
    finally:
        studio.close()
        studio.deleteLater()

    # Step 2 upfront controls & override propagation
    win = mw_mod.MainWindow()
    try:
        idx_hd = win.init_hook_design_combo.findData("slanted_pill")
        assert idx_hd >= 0
        win.init_hook_design_combo.setCurrentIndex(idx_hd)
        idx_im = win.init_intro_mode_combo.findData("freeze_blur")
        win.init_intro_mode_combo.setCurrentIndex(idx_im)
        win.opening_tts_chk.setChecked(False)
        ov = win._collect_initial_style_overrides("podcast")
        assert ov["hook_design_style"] == "slanted_pill"
        assert ov["opening_intro_mode"] == "freeze_blur"
        assert ov["opening_tts_enabled"] is False

        c = build_preset_config("podcast")
        apply_initial_style_overrides(c, ov)
        assert c.overlays.hook_banner.design_style == "slanted_pill"
        assert c.overlays.hook_banner.opening_intro_mode == "freeze_blur"
        assert c.timing.opening_tts_enabled is False
    finally:
        win.close()
        win.deleteLater()
