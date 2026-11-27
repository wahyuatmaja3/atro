"""Tests for Interactive Gaming Meme Shorts: existing SFX context catalog, interactive zoom, reaction cutaways, templates, UI, and real FFmpeg render."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from atro.config import AppSettings
from atro.core.composer_schema import (
    ClipComposerConfig,
    ComposerSfxCue,
    CropBoxConfig,
    FaceAnchorBox,
    ShotCropSegment,
    TransitionConfig,
)
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies
from atro.core.presets import READY_TEMPLATES, build_ready_template_config
from atro.core.schemas import (
    DownloadResult,
    JobRequest,
    TranscriptSegment,
    TranscriptWord,
)
from atro.core.template_packs import (
    load_all_template_packs,
    recommend_templates_for_context,
    render_template_thumbnail_qimage,
)
from atro.modules.analyzer.prompts import build_system_instruction
from atro.modules.analyzer.validator import build_heuristic_sfx_cues
from atro.modules.cutter import composer_renderer as cr
from atro.modules.cutter import sfx
from atro.modules.cutter.backsound import build_audio_filter_chain
from atro.modules.cutter.framing.face_tracking import FaceBox
from atro.modules.cutter.service import CutterService
from atro.modules.cutter.subtitle_anchors import (
    evaluate_zoom_at_time,
    resolve_interactive_zoom_windows,
)


def _w(word: str, s: float, e: float) -> TranscriptWord:
    return TranscriptWord(word=word, start=s, end=e, score=0.95)


def test_all_existing_sfx_have_gaming_context_specs():
    """Every existing audio asset in atro/assets/sfx must be documented in SFX_GAMING_CONTEXT_CATALOG."""
    custom_names = sfx.list_custom_sfx_names()
    assert len(custom_names) >= 50  # Repo ships 54 custom SFX files

    catalog = sfx.list_sfx_context_catalog()
    for stem in custom_names:
        spec = sfx.get_sfx_context(stem)
        assert spec is not None, f"SFX {stem!r} belum terdaftar di SFX_GAMING_CONTEXT_CATALOG"
        assert spec.category
        assert spec.usage_id
        assert spec.zoom_role in ("facecam", "gameplay", "center", "both", "none")
        assert 0.1 < spec.default_volume <= 1.0
        assert 0.5 <= spec.max_stinger_sec <= 6.0

    for builtin_name in sfx.BUILTIN_SFX_NAMES:
        spec = sfx.get_sfx_context(builtin_name)
        assert spec is not None
        assert spec.is_transition_sfx is True

    prompt_catalog = sfx.format_sfx_catalog_for_prompt(sfx.list_all_sfx_names(), edit_format="game")
    assert "Vine Boom:" in prompt_catalog
    assert "Arabic Nokia Ringtone:" in prompt_catalog
    assert "[transisi]" in prompt_catalog
    assert len(catalog) >= len(custom_names) + len(sfx.BUILTIN_SFX_NAMES)


def test_gaming_sfx_heuristic_matches_proper_context():
    """Heuristic SFX picker should choose right gaming meme SFX based on transcript meaning."""
    avail = sfx.list_all_sfx_names()
    segs = [
        TranscriptSegment(index=0, start=0.2, end=2.0, text="Gila sumpah clutch gacor cuy ez win!"),
        TranscriptSegment(index=1, start=3.2, end=4.8, text="Kok sus banget ada impostor di belakang?"),
        TranscriptSegment(index=2, start=6.0, end=7.6, text="Waduh wifi disconnect dc lag parah!"),
        TranscriptSegment(index=3, start=9.0, end=10.6, text="Anjir kalah bonk jatuh ke jurang!"),
    ]
    cues = build_heuristic_sfx_cues(segs, 0.0, 12.0, avail, edit_format="game")
    assert len(cues) >= 3
    by_seg_names = {c.sfx_name for c in cues}
    # Check specific semantic matches matching bundled SFX catalog
    assert any(n in by_seg_names for n in ("Bass Drop", "Japanese Drum", "Crowd Applause"))
    assert "Among Us SUS" in by_seg_names
    assert "Discord Leave" in by_seg_names
    assert any(n in by_seg_names for n in ("Bonk", "Lego Break Fall Apart", "Metal Pipe", "Splat"))

    # Even without explicit trigger words, game mode still assigns punch/exclamation SFX on dramatic lines
    plain_segs = [
        TranscriptSegment(index=0, start=0.5, end=2.5, text="Liat kanan kiri tahan posisi sekarang!"),
        TranscriptSegment(index=1, start=5.0, end=7.0, text="Eh kok bisa begitu sih barusan?"),
    ]
    plain_cues = build_heuristic_sfx_cues(plain_segs, 0.0, 8.0, avail, edit_format="game")
    assert len(plain_cues) >= 1

    # Prompt embeds SFX catalog for LLM
    sys_prompt = build_system_instruction(
        JobRequest(url="https://example.com/v", edit_format="game"),
        video_duration_sec=180.0,
        available_sfx=avail,
    )
    assert "Arabic Nokia Ringtone" in sys_prompt
    assert "GAMING" in sys_prompt


def test_backsound_stinger_trim_for_long_sfx():
    """Long meme SFX (e.g. 24s song/ringtone) must be trimmed & faded out cleanly."""
    lines, out_lbl = build_audio_filter_chain(
        has_source_audio=True,
        has_backsound=False,
        duration_sec=10.0,
        bgm_volume=0.14,
        enable_ducking=False,
        enable_voice_polish=True,
        transition_sfx_events=[
            (1.5, 1, 0.80, 2.2),
            (4.0, 2, 0.65, None),
        ],
    )
    joined = "\n".join(lines)
    assert "atrim=0:2.200,asetpts=PTS-STARTPTS,afade=t=out:st=1.716:d=0.484,volume=0.800,adelay=1500|1500" in joined
    assert "volume=0.650,adelay=4000|4000" in joined
    assert out_lbl == "aout"


def test_interactive_zoom_curves_anchors_and_preview_parity():
    """Interactive zoom windows support smooth_in_out/snap_out curves & facecam vs gameplay anchors."""
    cfg = build_ready_template_config("gaming_interactive_meme", start_time=0.0, end_time=12.0, padding_sec=0.0)
    cfg.layout.top_crop = CropBoxConfig(auto_detect=False, x=0.70, y=0.05, w=0.25, h=0.35)
    cfg.layout.shot_crops = [
        ShotCropSegment(
            t_start=0.0,
            t_end=12.0,
            top_crop=cfg.layout.top_crop,
            bottom_crop=CropBoxConfig(auto_detect=False, x=0.22, y=0.0, w=0.56, h=1.0),
            shot_mode="pip_facecam",
            speaker_faces={"SPK_1": FaceAnchorBox(x=0.78, y=0.14, w=0.10, h=0.16, confidence=0.92)},
        )
    ]
    cfg.audio.sfx_cues = [
        ComposerSfxCue(time_sec=2.0, sfx_name="Vine Boom", volume=0.8),
        ComposerSfxCue(time_sec=6.0, sfx_name="Metal Gear Solid Alert", volume=0.8),
    ]

    wins = resolve_interactive_zoom_windows(cfg, 12.0)
    assert len(wins) >= 2
    assert wins[0].curve == "smooth_in_out"
    # Vine Boom has zoom_role='facecam' -> top_right PiP anchor (x > 0.6, y < 0.35)
    z_boom, ax_boom, ay_boom = evaluate_zoom_at_time(wins, 2.0 + 0.35)
    assert z_boom > 1.05
    assert ax_boom > 0.60 and ay_boom < 0.35

    # Smooth curve ramps up gradually rather than jumping straight to peak at t_start
    z_edge, _, _ = evaluate_zoom_at_time(wins, wins[0].t_start + 0.02)
    assert 1.0 <= z_edge < z_boom

    # Metal Gear Solid Alert has zoom_role='gameplay' -> center gameplay anchor
    z_alert, ax_alert, ay_alert = evaluate_zoom_at_time(wins, 6.0 + 0.35)
    assert z_alert > 1.05
    assert abs(ax_alert - 0.5) < 0.05

    # Also test snap_out template
    cfg_split = build_ready_template_config("gaming_split_interaktif", start_time=0.0, end_time=12.0, padding_sec=0.0)
    cfg_split.audio.sfx_cues = [ComposerSfxCue(time_sec=2.5, sfx_name="Vine Boom", volume=0.8)]
    wins_split = resolve_interactive_zoom_windows(cfg_split, 12.0)
    assert wins_split and wins_split[0].curve == "snap_out"
    _, ax_sp, ay_sp = evaluate_zoom_at_time(wins_split, 2.5 + 0.20)
    assert ay_sp < 0.30  # Top facecam panel in 38:62 split

    # Render live preview frames (no crash, exact 1080x1920)
    cr.ensure_qt_gui_app()
    fake_bgr = np.full((360, 640, 3), (32, 48, 84), dtype=np.uint8)
    q1 = cr.render_preview_frame(fake_bgr, cfg, rel_time_sec=2.25, segments=[], clip_start_sec=0.0, clip_end_sec=12.0)
    q2 = cr.render_preview_frame(fake_bgr, cfg_split, rel_time_sec=2.70, segments=[], clip_start_sec=0.0, clip_end_sec=12.0)
    assert (q1.width(), q1.height()) == (1080, 1920)
    assert (q2.width(), q2.height()) == (1080, 1920)


def test_interactive_reaction_cutaways_with_and_without_facecam(monkeypatch, tmp_path: Path):
    """Detect shots alternating layout <-> single_focus reaction cutaways (and blurred fallback without webcam)."""
    cfg = build_ready_template_config("gaming_interactive_meme", start_time=0.0, end_time=10.0, padding_sec=0.0)
    cues = [ComposerSfxCue(time_sec=3.0, sfx_name="Vine Boom", volume=0.8)]
    segs_tr = [TranscriptSegment(index=0, start=2.8, end=4.2, text="Anjir kaget banget bro!")]

    monkeypatch.setattr(
        cr,
        "_detect_streamer_face_sync",
        lambda _ctx: FaceBox(x=0.75, y=0.06, w=0.14, h=0.22, conf=0.92),
    )
    shots_face = cr.detect_composer_shot_crops_sync(
        source_path=tmp_path / "dummy.mp4",
        start_sec=0.0,
        end_sec=10.0,
        mode=cfg.layout.mode,
        layout=cfg.layout,
        transcript_segments=segs_tr,
        sfx_cues=cues,
    )
    assert len(shots_face) >= 3
    assert any(s.shot_mode == "pip_facecam" for s in shots_face)
    rx_shots = [s for s in shots_face if s.shot_mode == "single_focus" and s.reason == "reaction"]
    assert rx_shots
    assert rx_shots[0].transition_in is not None and rx_shots[0].transition_in.type == "zoom_in"
    assert shots_face[-1].t_end == pytest.approx(10.0, abs=0.05)

    # Without webcam face: falls back cleanly to blurred_frame + gameplay zoom single_focus
    monkeypatch.setattr(cr, "_detect_streamer_face_sync", lambda _ctx: None)
    shots_nofc = cr.detect_composer_shot_crops_sync(
        source_path=tmp_path / "dummy.mp4",
        start_sec=0.0,
        end_sec=10.0,
        mode=cfg.layout.mode,
        layout=cfg.layout,
        transcript_segments=segs_tr,
        sfx_cues=cues,
    )
    assert len(shots_nofc) >= 3
    assert any(s.shot_mode == "blurred_frame" for s in shots_nofc)
    assert any(s.shot_mode == "single_focus" and s.reason == "reaction" for s in shots_nofc)


def test_templates_registered_and_recommended_for_game():
    """New Gaming Interactive templates appear in Gaming category & preserve backwards-compatible recs."""
    packs = load_all_template_packs()
    for tid in ("gaming_interactive_meme", "gaming_split_interaktif"):
        assert tid in packs
        assert tid in READY_TEMPLATES
        assert packs[tid]["category"] == "Gaming"
        thumb = render_template_thumbnail_qimage(tid)
        assert thumb.width() == 135 and thumb.height() == 240

    recs = recommend_templates_for_context(title="Clutch Valorant Ranked", edit_format="game", packs=packs)
    assert "gaming_interactive_meme" in recs
    assert "gaming_split" in recs


@pytest.mark.asyncio
async def test_ffmpeg_e2e_gaming_interactive_meme_and_split(tmp_path: Path):
    """Real FFmpeg render mixing pip_facecam/split_screen <-> single_focus zoom cutaways + smooth zoom + trimmed SFX."""
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin", backsound_dir=tmp_path / "bgm")
    pre = check_startup_dependencies(settings)
    if not pre.ffmpeg.available or not pre.ffprobe.available:
        pytest.skip("FFmpeg/FFprobe tidak tersedia")

    src_mp4 = tmp_path / "gaming_src.mp4"
    mk = subprocess.run(
        [
            pre.ffmpeg.path, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=7.0",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=7.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(src_mp4),
        ],
        capture_output=True,
        text=True,
    )
    assert mk.returncode == 0 and src_mp4.is_file()

    dl = DownloadResult(
        job_id="j_game",
        video_id="v_game",
        title="Clutch Valorant Meme",
        duration_sec=7.0,
        width=640,
        height=360,
        fps=25.0,
        has_audio=True,
        source_video_path=str(src_mp4),
    )
    segs = [
        TranscriptSegment(
            index=0,
            start=0.4,
            end=6.5,
            text="Gila anjir clutch ini menang!",
            words=[
                _w("Gila", 0.5, 1.1),
                _w("anjir", 1.2, 1.9),
                _w("clutch", 2.6, 3.3),
                _w("ini", 3.5, 4.1),
                _w("menang!", 5.0, 6.2),
            ],
        )
    ]

    svc = CutterService()
    for tid, base_mode, sfx_list in (
        ("gaming_interactive_meme", "pip_facecam", ["Vine Boom", "Bonk"]),
        ("gaming_split_interaktif", "split_screen", ["Bruh", "Arabic Nokia Ringtone"]),
    ):
        cfg = build_ready_template_config(tid, clip_title=f"Tes {tid}", start_time=0.0, end_time=7.0, padding_sec=0.0)
        cfg.audio.backsound_enabled = False
        cfg.timing.silence_trim_enabled = False  # Test mixed-shot timeline deterministically across full 7s
        cfg.layout.shot_crops = [
            ShotCropSegment(
                t_start=0.0,
                t_end=2.4,
                top_crop=CropBoxConfig(auto_detect=False, x=0.72, y=0.05, w=0.24, h=0.35),
                bottom_crop=CropBoxConfig(auto_detect=False, x=0.20, y=0.0, w=0.60, h=1.0),
                shot_mode=base_mode,  # type: ignore[arg-type]
                speaker_faces={"SPK_1": FaceAnchorBox(x=0.80, y=0.14, w=0.10, h=0.15, confidence=0.92)},
            ),
            ShotCropSegment(
                t_start=2.4,
                t_end=4.2,
                top_crop=CropBoxConfig(auto_detect=False, x=0.68, y=0.02, w=0.30, h=0.50),
                bottom_crop=CropBoxConfig(auto_detect=False, x=0.20, y=0.0, w=0.60, h=1.0),
                shot_mode="single_focus",
                reason="reaction",
                transition_in=TransitionConfig(type="zoom_in", mode="fixed", duration_sec=0.18, easing="ease_out"),
                speaker_faces={"SPK_1": FaceAnchorBox(x=0.80, y=0.14, w=0.10, h=0.15, confidence=0.92)},
            ),
            ShotCropSegment(
                t_start=4.2,
                t_end=7.0,
                top_crop=CropBoxConfig(auto_detect=False, x=0.72, y=0.05, w=0.24, h=0.35),
                bottom_crop=CropBoxConfig(auto_detect=False, x=0.20, y=0.0, w=0.60, h=1.0),
                shot_mode=base_mode,  # type: ignore[arg-type]
                transition_in=TransitionConfig(type="zoom_out", mode="fixed", duration_sec=0.20, easing="ease_in_out"),
                speaker_faces={"SPK_1": FaceAnchorBox(x=0.80, y=0.14, w=0.10, h=0.15, confidence=0.92)},
            ),
        ]
        cfg.audio.sfx_cues = [
            ComposerSfxCue(time_sec=2.3, sfx_name=sfx_list[0], volume=0.8),
            ComposerSfxCue(time_sec=4.8, sfx_name=sfx_list[1], volume=0.75),
        ]

        job_dir = tmp_path / f"job_{tid}"
        job_dir.mkdir(parents=True, exist_ok=True)
        logger = JobLogger(f"j_{tid}", job_dir / "job.log")

        rendered = await svc.render_clip_with_composer_config(
            job_id=f"j_{tid}",
            download=dl,
            config=cfg,
            job_dir=job_dir,
            preflight=pre,
            logger=logger,
            transcript_segments=segs,
        )
        out_mp4 = Path(rendered.output_path)
        assert out_mp4.is_file() and out_mp4.stat().st_size > 2000

        probe = subprocess.run(
            [
                pre.ffprobe.path, "-v", "error",
                "-show_entries", "stream=codec_type,width,height",
                "-of", "json", str(out_mp4),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        streams = json.loads(probe.stdout).get("streams", [])
        v_streams = [s for s in streams if s.get("codec_type") == "video"]
        a_streams = [s for s in streams if s.get("codec_type") == "audio"]
        assert len(v_streams) == 1 and v_streams[0]["width"] == 1080 and v_streams[0]["height"] == 1920
        assert len(a_streams) == 1
        assert list((job_dir / "clips").glob("_cmp_*")) == []


def test_ui_gaming_interactive_studio_and_home(tmp_path: Path, monkeypatch):
    """Studio, MainWindow, and HomeQuickClipPage expose & sync gaming interactive zoom/SFX/cutaway controls."""
    from atro.ui import main_window as mw_mod
    from atro.ui.widgets.clip_studio import ClipStudioWidget

    cr.ensure_qt_gui_app()
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin", backsound_dir=tmp_path / "bgm")
    monkeypatch.setattr(mw_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(mw_mod, "save_settings", lambda _s: None)

    win = mw_mod.MainWindow()
    try:
        win._select_initial_style("gaming_interactive_meme")
        ov = win._collect_initial_style_overrides("game")
        assert ov["ready_template_id"] == "gaming_interactive_meme"
        assert "gaming_interactive_meme" in win.home_page._quick_tpl_buttons
    finally:
        win.close()
        win.deleteLater()

    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        studio._transcript_segments = [
            TranscriptSegment(index=0, start=0.5, end=2.5, text="Gila clutch banget barusan bro!"),
        ]
        studio.apply_preset("gaming_interactive_meme")
        cfg = studio.current_config()
        assert cfg.layout.interactive_reaction_cuts is True
        assert cfg.overlays.climax_effects.zoom_curve == "smooth_in_out"
        assert cfg.overlays.climax_effects.zoom_sync_sfx is True
        assert cfg.overlays.climax_effects.zoom_target == "auto_context"
        assert len(cfg.audio.sfx_cues) >= 1
        assert studio.interactive_cuts_chk.isChecked() is True
        assert studio.zoom_curve_combo.currentData() == "smooth_in_out"
        assert studio.zoom_target_combo.currentData() == "auto_context"
        assert studio.zoom_sync_sfx_chk.isChecked() is True

        # Mutate via UI widgets
        snap_idx = studio.zoom_curve_combo.findData("snap_out")
        studio.zoom_curve_combo.setCurrentIndex(snap_idx)
        studio.interactive_cuts_chk.setChecked(False)
        mutated = studio.current_config()
        assert mutated.overlays.climax_effects.zoom_curve == "snap_out"
        assert mutated.layout.interactive_reaction_cuts is False

        # SFX context tooltip populated on cue combo items
        sfx_w = studio.sfx_cues_table.cellWidget(0, 1)
        assert sfx_w is not None
        assert sfx_w.itemData(0, 3)  # Qt.ItemDataRole.ToolTipRole == 3
    finally:
        studio.close()
        studio.deleteLater()
