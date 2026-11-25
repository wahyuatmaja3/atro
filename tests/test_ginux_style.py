"""Tests for GiNUX-style features: G1 templates, G2 jump-cut, G3 repeating zoom, G4a/G4b contextual SFX, G8 above-speaker captions."""

from __future__ import annotations

import asyncio
from pathlib import Path

import cv2
import numpy as np
import pytest

from atro.config import AppSettings
from atro.core.composer_schema import (
    ClipComposerConfig,
    ComposerSfxCue,
    CropBoxConfig,
    FaceAnchorBox,
    LayoutLayerConfig,
    ShotCropSegment,
    SpeakerTurnSpan,
    TranscriptSegmentOverride,
)
from atro.core.logging_utils import JobLogger
from atro.core.preflight import check_startup_dependencies
from atro.core.presets import build_preset_config, build_ready_template_config
from atro.core.schemas import (
    DownloadResult,
    GeminiClipItem,
    GeminiClipSelectionSchema,
    GeminiSfxCueItem,
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
from atro.modules.analyzer.validator import build_heuristic_sfx_cues, validate_and_sanitize_clips
from atro.modules.cutter import sfx
from atro.modules.cutter.composer_renderer import (
    compile_composer_ffmpeg_plan,
    ensure_qt_gui_app,
    render_preview_frame,
    zoom_punch_windows,
)
from atro.modules.cutter.framing.diarization import SpeakerSegment
from atro.modules.cutter.framing.face_identity import DetectedFaceObs
from atro.modules.cutter.framing.face_tracking import FaceBox
from atro.modules.cutter.framing.shot_planner import _SampleFrame, plan_podcast_shots_from_observations
from atro.modules.cutter.jump_cut import (
    build_trim_ffmpeg_command,
    collect_words,
    plan_keep_ranges,
    remap_segments,
    remap_time,
    trimmed_duration,
)
from atro.modules.cutter.service import CutterService, apply_initial_style_overrides
from atro.modules.cutter.subtitle_anchors import resolve_chunks_anchor_positions
from atro.modules.cutter.subtitle_burner import build_composer_caption_chunks, generate_composer_ass_file


def _w(word: str, s: float, e: float) -> TranscriptWord:
    return TranscriptWord(word=word, start=s, end=e)


WORDS = [
    _w("halo", 0.0, 0.4),
    _w("eee", 0.5, 1.0),  # filler
    _w("guys", 1.1, 1.5),
    _w("lama", 3.5, 3.9),  # 2.0s pause before
    _w("banget", 4.0, 4.5),
]


def test_plan_keep_ranges_removes_pause_and_filler():
    ranges = plan_keep_ranges(WORDS, 0.0, 5.0, max_gap_sec=0.35, remove_fillers=True)
    assert ranges, "expected cuts"
    assert trimmed_duration(ranges) < 5.0 - 0.4
    assert remap_time(2.5, ranges) is None
    assert remap_time(0.75, ranges) is None
    assert remap_time(1.3, ranges) is not None
    assert remap_time(4.2, ranges) is not None
    assert ranges[0].new_start == 0.0
    for a, b in zip(ranges, ranges[1:]):
        assert abs((a.new_start + a.length) - b.new_start) < 0.002


def test_plan_keep_ranges_noop_when_nothing_to_cut():
    tight = [_w("a", 0.0, 0.5), _w("b", 0.55, 1.0), _w("c", 1.05, 1.5)]
    assert plan_keep_ranges(tight, 0.0, 1.6) == []
    assert plan_keep_ranges([], 0.0, 5.0) == []


def test_remap_segments_shifts_words_and_drops_filler():
    seg = TranscriptSegment(index=0, start=0.0, end=4.5, text="halo eee guys lama banget", words=WORDS)
    ranges = plan_keep_ranges(WORDS, 0.0, 5.0, 0.35, True)
    out = remap_segments([seg], ranges, True)
    assert len(out) == 1
    assert [w.word for w in out[0].words] == ["halo", "guys", "lama", "banget"]
    assert out[0].start >= 0.0 and out[0].end <= trimmed_duration(ranges) + 0.01
    assert "eee" not in out[0].text
    assert all(b.start >= a.end - 1e-6 for a, b in zip(out[0].words, out[0].words[1:]))


def test_collect_words_window():
    seg = TranscriptSegment(index=0, start=0.0, end=4.5, text="x", words=WORDS)
    got = collect_words([seg], 3.0, 5.0)
    assert [w.word for w in got] == ["lama", "banget"]


def test_trim_command_shape(tmp_path):
    ranges = plan_keep_ranges(WORDS, 0.0, 5.0, 0.35, True)
    cmd, script = build_trim_ffmpeg_command(
        tmp_path / "ffmpeg.exe", tmp_path / "in.mp4", tmp_path / "out.mp4", 0.0, 5.0, ranges,
        tmp_path / "f.txt", True, 30.0,
    )
    assert f"concat=n={len(ranges)}:v=1:a=1" in script
    assert "[vout]" in script and "-filter_complex_script" in cmd
    _, script_na = build_trim_ffmpeg_command(
        tmp_path / "ffmpeg.exe", tmp_path / "in.mp4", tmp_path / "out.mp4", 0.0, 5.0, ranges,
        tmp_path / "f.txt", False, 30.0,
    )
    assert "a=0" in script_na and "atrim" not in script_na


def test_zoom_punch_windows_repeat():
    cfg = ClipComposerConfig()
    cl = cfg.overlays.climax_effects
    cl.zoom_punch_enabled = True
    cl.zoom_punch_start_sec = 1.0
    cl.zoom_punch_duration_sec = 1.0
    assert len(zoom_punch_windows(cl, 20.0)) == 1
    cl.zoom_punch_repeat_sec = 4.0
    wins = zoom_punch_windows(cl, 20.0)
    assert wins[0] == (1.0, 2.0) and wins[1] == (5.0, 6.0)
    assert len(wins) == 5
    assert zoom_punch_windows(cl, 20.0, shift=2.0)[0] == (3.0, 4.0)
    cl.zoom_punch_enabled = False
    assert zoom_punch_windows(cl, 20.0) == []


def test_custom_sfx_resolution_and_bundled_memes(tmp_path, monkeypatch):
    (tmp_path / "vine_boom.mp3").write_bytes(b"x" * 2048)
    monkeypatch.setattr(sfx, "get_custom_sfx_dirs", lambda: [tmp_path])
    assert sfx.resolve_transition_sfx_path("vine_boom") == tmp_path / "vine_boom.mp3"
    assert sfx.resolve_transition_sfx_path("Vine Boom") == tmp_path / "vine_boom.mp3"
    assert sfx.resolve_transition_sfx_path("tidak_ada") is None
    assert sfx.resolve_transition_sfx_path("none") is None
    assert "vine_boom" in sfx.list_custom_sfx_names()
    monkeypatch.undo()
    # User's real meme SFX files in atro/assets/sfx/ resolve cleanly (even with spaces/parens)
    if (sfx.get_builtin_sfx_dir() / "Vine Boom.mp3").is_file():
        assert sfx.resolve_transition_sfx_path("Vine Boom") is not None
        assert sfx.resolve_transition_sfx_path("vine_boom") is not None


def test_ginux_templates_available_applied_and_isolated():
    packs = load_all_template_packs()
    for tid in ("ginux_clip_meme", "ginux_reaction_zoom", "ginux_drama_banner"):
        assert tid in packs
        cfg = build_ready_template_config(tid, clip_title="Tes", start_time=0.0, end_time=30.0)
        assert cfg.timing.silence_trim_enabled is True
        assert cfg.overlays.climax_effects.zoom_punch_repeat_sec > 0
        assert cfg.subtitles.placement_preset == "above_speaker"
        assert cfg.subtitles.font_family == "Impact"
        assert cfg.subtitles.highlight_color_hex == "#FACC15"
        assert cfg.audio.contextual_sfx_enabled is True
    meme = build_ready_template_config("ginux_clip_meme")
    assert meme.timing.silence_max_gap_sec == 0.30
    assert meme.layout.transition.sfx == "pop"

    # Initial overrides via Step 2 propagate timing & contextual SFX flags
    base_cfg = build_preset_config("podcast")
    apply_initial_style_overrides(base_cfg, {"ready_template_id": "ginux_clip_meme"})
    assert base_cfg.timing.silence_trim_enabled is True
    assert base_cfg.timing.silence_max_gap_sec == 0.30
    assert base_cfg.audio.contextual_sfx_enabled is True
    assert base_cfg.subtitles.placement_preset == "above_speaker"

    # Legacy templates remain untouched (default off)
    pod = build_ready_template_config("podcast")
    assert pod.timing.silence_trim_enabled is False
    assert pod.audio.contextual_sfx_enabled is False
    assert pod.overlays.climax_effects.zoom_punch_repeat_sec == 0.0
    assert pod.subtitles.placement_preset == "bottom_safe"

    # Gallery thumbnail renders cleanly with above_speaker
    thumb = render_template_thumbnail_qimage("ginux_clip_meme", width=120, height=214)
    assert thumb.width() == 120 and thumb.height() == 214
    assert "ginux_clip_meme" in recommend_templates_for_context(title="Ngakak komedi podcast")


def test_g4b_contextual_sfx_prompt_validator_and_heuristic():
    req = JobRequest(source_type="local_file", local_video_path="a.mp4", clip_count=1)
    avail = ["pop", "Vine Boom", "Cash Register (Kaching)", "Awkward"]
    prompt = build_system_instruction(req, 120.0, available_sfx=avail)
    assert "sfx_cues" in prompt
    assert "Vine Boom" in prompt
    assert "Cash Register (Kaching)" in prompt

    raw = GeminiClipSelectionSchema(
        clips=[
            GeminiClipItem(
                title="Plot Twist Cuan Besar",
                start_time=10.0,
                end_time=35.0,
                virality_score=9,
                reason="Hook tajam dan punchline kaget",
                sfx_cues=[
                    GeminiSfxCueItem(t_sec=14.2, sfx_name="vine_boom", reason="Kaget"),
                    GeminiSfxCueItem(t_sec=14.9, sfx_name="Awkward", reason="Terlalu dekat, harus dedupe"),
                    GeminiSfxCueItem(t_sec=22.5, sfx_name="Cash Register (Kaching)", reason="Bahasan cuan"),
                    GeminiSfxCueItem(t_sec=25.0, sfx_name="sfx_fiktif_tidak_ada", reason="Harus dibuang"),
                ],
            )
        ]
    )
    clips, _warns = validate_and_sanitize_clips(raw, 120.0, req, available_sfx=avail)
    assert len(clips) == 1
    assert [c.sfx_name for c in clips[0].sfx_cues] == ["Vine Boom", "Cash Register (Kaching)"]

    segs = [
        TranscriptSegment(index=0, start=10.0, end=13.0, text="Ternyata omzetnya seratus juta cuan banget!"),
        TranscriptSegment(index=1, start=15.0, end=18.0, text="Semua langsung diam canggung krik krik."),
    ]
    heur = build_heuristic_sfx_cues(segs, 10.0, 20.0, avail)
    assert len(heur) == 2
    assert heur[0].sfx_name == "Cash Register (Kaching)"
    assert heur[1].sfx_name == "Awkward"


def test_g8_above_speaker_zoomed_out_instant_switch_and_wysiwyg(tmp_path: Path):
    ensure_qt_gui_app()
    cfg = build_ready_template_config("ginux_clip_meme", start_time=0.0, end_time=6.0, padding_sec=0.0)
    cfg.layout.mode = "blurred_frame"
    cfg.layout.blurred_frame_aspect = "16:9"
    cfg.layout.top_crop = CropBoxConfig(auto_detect=True, x=0.0, y=0.0, w=1.0, h=1.0)
    # Two speakers visible in zoomed-out 16:9 frame: SPK_1 on left (x=0.12), SPK_2 on right (x=0.72)
    cfg.layout.shot_crops = [
        ShotCropSegment(
            t_start=0.0,
            t_end=6.0,
            top_crop=CropBoxConfig(auto_detect=True, x=0.0, y=0.0, w=1.0, h=1.0),
            shot_mode="blurred_frame",
            speaker_id="SPK_1",
            confidence=0.92,
            speaker_faces={
                "SPK_1": FaceAnchorBox(x=0.12, y=0.30, w=0.14, h=0.26, confidence=0.95),
                "SPK_2": FaceAnchorBox(x=0.74, y=0.32, w=0.14, h=0.26, confidence=0.95),
            },
        )
    ]
    cfg.layout.speaker_turns = [
        SpeakerTurnSpan(t_start=0.0, t_end=2.0, speaker_id="SPK_1", confidence=0.92),
        SpeakerTurnSpan(t_start=2.0, t_end=4.0, speaker_id="SPK_2", confidence=0.92),
        SpeakerTurnSpan(t_start=4.0, t_end=6.0, speaker_id="SPK_1", confidence=0.92),
    ]
    segs = [
        TranscriptSegment(
            index=0,
            start=0.0,
            end=5.8,
            text="Aku gak setuju. Salah kamu sendiri! Buktiin dong sekarang.",
            words=[
                _w("Aku", 0.1, 0.5),
                _w("gak", 0.5, 0.9),
                _w("setuju.", 0.9, 1.8),
                _w("Salah", 2.1, 2.5),
                _w("kamu", 2.5, 3.0),
                _w("sendiri!", 3.0, 3.8),
                _w("Buktiin", 4.1, 4.6),
                _w("dong", 4.6, 5.1),
                _w("sekarang.", 5.1, 5.7),
            ],
        )
    ]

    from atro.modules.cutter.subtitle_burner import compute_caption_line_layout

    chunks = build_composer_caption_chunks(cfg, segs, clip_start_sec=0.0, clip_end_sec=6.0)
    assert len(chunks) >= 3
    box_sizes = [
        (
            (bl := compute_caption_line_layout(cfg, [w.text for w in c.words], -1, 1080, 1920)).box_w,
            bl.box_h,
        )
        for c in chunks
    ]
    anchors = resolve_chunks_anchor_positions(chunks, cfg, src_w=1280, src_h=720, chunk_box_sizes=box_sizes)
    # Chunk 0 (SPK_1 left) < 420, Chunk 2 (SPK_2 right) > 660, Chunk 4 (SPK_1 back left) < 420
    left_xs = [anchors[i][0] for i, c in enumerate(chunks) if c.end <= 2.0]
    right_xs = [anchors[i][0] for i, c in enumerate(chunks) if 2.0 <= c.start and c.end <= 4.0]
    back_left_xs = [anchors[i][0] for i, c in enumerate(chunks) if c.start >= 4.0]
    assert left_xs and all(x < 450 for x in left_xs)
    assert right_xs and all(x > 630 for x in right_xs)
    assert back_left_xs and all(x < 450 for x in back_left_xs)

    # 16:9 box inside 1080x1920 sits at y=656..1264; face top y=0.30 is at y=656+0.30*608=838 -> caption sits above (< 810)
    assert all(100 <= y < 820 for _x, y in anchors)

    # ASS contains exact per-chunk \pos(x,y) matching resolve_chunks_anchor_positions
    ass_path = generate_composer_ass_file(tmp_path / "above.ass", cfg, segs, clip_duration_sec=6.0, src_w=1280, src_h=720)
    assert ass_path is not None
    ass_text = ass_path.read_text(encoding="utf-8")
    assert f"\\pos({int(round(left_xs[0]))}," in ass_text
    assert f"\\pos({int(round(right_xs[0]))}," in ass_text

    # WYSIWYG Preview QImage matches: yellow caption text appears on LEFT at t=0.8s and on RIGHT at t=2.8s
    frame = np.full((720, 1280, 3), (30, 30, 30), dtype=np.uint8)
    img_left = render_preview_frame(frame, cfg, rel_time_sec=0.8, segments=segs, clip_start_sec=0.0, clip_end_sec=6.0)
    img_right = render_preview_frame(frame, cfg, rel_time_sec=2.8, segments=segs, clip_start_sec=0.0, clip_end_sec=6.0)
    assert img_left.width() == 1080 and img_left.height() == 1920

    def _white_pixel_count(qimg: object, x0: int, x1: int, y0: int, y1: int) -> int:
        ptr = qimg.bits()
        ptr.setsize(qimg.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape((qimg.height(), qimg.width(), 4))
        roi = arr[y0:y1, x0:x1, :3]
        return int(np.sum((roi[:, :, 0] > 180) & (roi[:, :, 1] > 180)))

    cy = int(round(anchors[0][1]))
    assert _white_pixel_count(img_left, 50, 480, max(0, cy - 80), cy + 80) > 200
    assert _white_pixel_count(img_left, 600, 1030, max(0, cy - 80), cy + 80) < 50
    cy_r = int(round(anchors[len(left_xs)][1]))
    assert _white_pixel_count(img_right, 600, 1030, max(0, cy_r - 80), cy_r + 80) > 200
    assert _white_pixel_count(img_right, 50, 480, max(0, cy_r - 80), cy_r + 80) < 50


def test_g8_zoom_punch_hysteresis_and_fallback():
    cfg = build_ready_template_config("ginux_clip_meme", start_time=0.0, end_time=6.0, padding_sec=0.0)
    cfg.layout.mode = "blurred_frame"
    cfg.layout.blurred_frame_aspect = "16:9"
    cfg.overlays.climax_effects.zoom_punch_enabled = True
    cfg.overlays.climax_effects.zoom_punch_start_sec = 0.0
    cfg.overlays.climax_effects.zoom_punch_duration_sec = 2.0
    cfg.overlays.climax_effects.zoom_punch_factor = 1.25
    cfg.layout.shot_crops = [
        ShotCropSegment(
            t_start=0.0,
            t_end=6.0,
            top_crop=CropBoxConfig(auto_detect=True, x=0.0, y=0.0, w=1.0, h=1.0),
            shot_mode="blurred_frame",
            speaker_id="SPK_1",
            confidence=0.9,
            speaker_faces={
                "SPK_1": FaceAnchorBox(x=0.20, y=0.35, w=0.12, h=0.24, confidence=0.95),
                "SPK_2": FaceAnchorBox(x=0.68, y=0.35, w=0.12, h=0.24, confidence=0.95),
            },
        )
    ]
    # Chunk 0 during zoom punch (SPK_1), Chunk 1 has low-confidence ambiguous overlap -> holds Chunk 0 pos!
    cfg.layout.speaker_turns = [
        SpeakerTurnSpan(t_start=0.0, t_end=2.0, speaker_id="SPK_1", confidence=0.90),
        SpeakerTurnSpan(t_start=2.0, t_end=3.5, speaker_id="SPK_1", confidence=0.25),
        SpeakerTurnSpan(t_start=2.0, t_end=3.5, speaker_id="SPK_2", confidence=0.25),
    ]
    segs = [
        TranscriptSegment(index=0, start=0.2, end=1.6, text="Halo", words=[_w("Halo", 0.2, 1.6)]),
        TranscriptSegment(index=1, start=2.2, end=3.4, text="Debat", words=[_w("Debat", 2.2, 3.4)]),
    ]
    chunks = build_composer_caption_chunks(cfg, segs, clip_start_sec=0.0, clip_end_sec=6.0)

    cfg_no_zoom = cfg.model_copy(deep=True)
    cfg_no_zoom.overlays.climax_effects.zoom_punch_enabled = False
    pos_zoom = resolve_chunks_anchor_positions(chunks, cfg, src_w=1280, src_h=720)
    pos_no_zoom = resolve_chunks_anchor_positions(chunks, cfg_no_zoom, src_w=1280, src_h=720)
    # Left speaker moves further away from center (540) during 1.25x zoom punch
    assert pos_zoom[0][0] < pos_no_zoom[0][0]
    # Ambiguous chunk 1 holds last confident position from chunk 0
    assert pos_zoom[1] == pos_zoom[0]

    # No-face fallback -> bottom_safe lower-middle (540, 1152)
    cfg_empty = cfg.model_copy(deep=True)
    cfg_empty.layout.shot_crops = []
    pos_fb = resolve_chunks_anchor_positions(chunks, cfg_empty, src_w=1280, src_h=720)
    assert pos_fb == [(540.0, 1152.0), (540.0, 1152.0)]


def test_shot_planner_populates_speaker_faces():
    samples = [
        _SampleFrame(
            rel_t=0.0,
            abs_t=0.0,
            angle_id=1,
            is_cut=False,
            faces=[
                DetectedFaceObs(box=FaceBox(0.15, 0.25, 0.12, 0.22, 0.95, 4.0), face_id="FACE_A"),
                DetectedFaceObs(box=FaceBox(0.72, 0.25, 0.12, 0.22, 0.95, 0.2), face_id="FACE_B"),
            ],
        ),
        _SampleFrame(
            rel_t=0.5,
            abs_t=0.5,
            angle_id=1,
            is_cut=False,
            faces=[
                DetectedFaceObs(box=FaceBox(0.15, 0.25, 0.12, 0.22, 0.95, 4.2), face_id="FACE_A"),
                DetectedFaceObs(box=FaceBox(0.72, 0.25, 0.12, 0.22, 0.95, 0.2), face_id="FACE_B"),
            ],
        ),
        _SampleFrame(
            rel_t=1.0,
            abs_t=1.0,
            angle_id=1,
            is_cut=False,
            faces=[
                DetectedFaceObs(box=FaceBox(0.15, 0.25, 0.12, 0.22, 0.95, 4.5), face_id="FACE_A"),
                DetectedFaceObs(box=FaceBox(0.72, 0.25, 0.12, 0.22, 0.95, 0.2), face_id="FACE_B"),
            ],
        ),
    ]
    diar = [SpeakerSegment(start=0.0, end=1.5, speaker_id="SPK_1", confidence=0.92)]
    layout = LayoutLayerConfig(mode="blurred_frame", group_mode="fit_all_blur")
    shots = plan_podcast_shots_from_observations(samples, 1.5, layout, diar)
    assert len(shots) == 1
    assert "SPK_1" in shots[0].speaker_faces
    assert "FACE_B" in shots[0].speaker_faces
    # Zoomed-out blurred_frame with 2 faces frames both faces wide
    assert shots[0].top_crop.w >= 0.70


@pytest.mark.asyncio
async def test_real_ffmpeg_ginux_jump_cut_contextual_sfx_and_above_speaker(tmp_path: Path):
    pre = check_startup_dependencies(AppSettings(workspace_dir=tmp_path / "ws"))
    if not pre.ffmpeg.available or not pre.ffmpeg.path:
        pytest.skip("FFmpeg tidak tersedia")

    # Build a 6-second 640x360 video with synthetic audio via FFmpeg
    src_mp4 = tmp_path / "src.mp4"
    import subprocess

    mk = subprocess.run(
        [
            pre.ffmpeg.path, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=#1e293b:s=640x360:d=6.0:r=25",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(src_mp4),
        ],
        capture_output=True,
        text=True,
    )
    assert mk.returncode == 0 and src_mp4.is_file()

    # Custom SFX asset
    sfx_dir = tmp_path / "sfx"
    sfx_dir.mkdir()
    vine_wav = sfx_dir / "boom.wav"
    subprocess.run(
        [
            pre.ffmpeg.path, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=110:duration=0.25",
            str(vine_wav),
        ],
        check=True,
        capture_output=True,
    )
    # Monkeypatch-free: copy to app or check plan directly, plus render real MP4 using built-in 'pop' + Vine Boom if present
    cfg = build_ready_template_config("ginux_clip_meme", clip_title="Tes GiNUX", start_time=0.0, end_time=6.0, padding_sec=0.0)
    cfg.audio.backsound_enabled = False
    cfg.layout.framing_tracking_mode = "manual_crop"
    cfg.layout.shot_crops = [
        ShotCropSegment(
            t_start=0.0,
            t_end=6.0,
            top_crop=CropBoxConfig(auto_detect=False, x=0.2, y=0.0, w=0.6, h=1.0),
            speaker_id="SPK_1",
            speaker_faces={"SPK_1": FaceAnchorBox(x=0.40, y=0.30, w=0.20, h=0.30, confidence=0.9)},
        )
    ]
    # Add two speech bursts separated by a 2.5s pause so jump-cut trims > 2.0s!
    segs = [
        TranscriptSegment(
            index=0,
            start=0.2,
            end=5.8,
            text="Halo eee guys plot twist!",
            words=[
                _w("Halo", 0.2, 0.7),
                _w("eee", 0.8, 1.3),
                _w("guys", 1.4, 1.8),
                _w("plot", 4.3, 4.9),
                _w("twist!", 5.0, 5.7),
            ],
        )
    ]
    # Cue at 4.8s is in speech (kept & remapped!), cue at 2.8s is in deleted silence (dropped!)
    sfx_name = "Vine Boom" if (sfx.get_builtin_sfx_dir() / "Vine Boom.mp3").is_file() else "pop"
    cfg.audio.sfx_cues = [
        ComposerSfxCue(time_sec=2.8, sfx_name=sfx_name, volume=0.7),
        ComposerSfxCue(time_sec=4.8, sfx_name=sfx_name, volume=0.8),
    ]

    # Verify compile_composer_ffmpeg_plan includes sfx_cues when contextual_sfx_enabled=True
    plan = compile_composer_ffmpeg_plan(
        config=cfg, src_w=640, src_h=360, fps=25.0, duration_sec=6.0, work_dir=tmp_path / "plan", segments=segs,
    )
    assert len(plan.sfx_events) >= 2

    dl = DownloadResult(
        job_id="j_ginux",
        video_id="v1",
        title="Tes",
        duration_sec=6.0,
        width=640,
        height=360,
        fps=25.0,
        has_audio=True,
        source_video_path=str(src_mp4),
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    logger = JobLogger("j_ginux", job_dir / "job.log")

    svc = CutterService()
    rendered = await svc.render_clip_with_composer_config(
        job_id="j_ginux",
        download=dl,
        config=cfg,
        job_dir=job_dir,
        preflight=pre,
        logger=logger,
        transcript_segments=segs,
    )
    out_mp4 = Path(rendered.output_path)
    assert out_mp4.is_file() and out_mp4.stat().st_size > 1000
    # Jump-cut shortened the 6.0s clip to < 4.0s!
    assert rendered.output_duration_sec < 4.0
    assert rendered.subtitles_burned is True
    # Saved config keeps original settings (not trimmed 0..new_dur)
    saved_cfg = ClipComposerConfig.model_validate_json(Path(rendered.composer_config_path).read_text(encoding="utf-8"))
    assert saved_cfg.timing.silence_trim_enabled is True
    assert saved_cfg.timing.end_time == 6.0


def test_ui_ginux_controls_step2_and_studio(tmp_path: Path, monkeypatch):
    from atro.ui import main_window as mw_mod
    from atro.ui.widgets.clip_studio import ClipStudioWidget

    ensure_qt_gui_app()
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin", backsound_dir=tmp_path / "bgm")
    monkeypatch.setattr(mw_mod, "load_settings", lambda: settings)
    monkeypatch.setattr(mw_mod, "save_settings", lambda _s: None)

    win = mw_mod.MainWindow()
    try:
        win._select_initial_style("ginux_clip_meme")
        assert win.init_sub_pos_combo.currentData() == "above_speaker"
        assert win.init_zoom_punch_chk.isChecked() is True
        ov = win._collect_initial_style_overrides("podcast")
        assert ov["ready_template_id"] == "ginux_clip_meme"
        assert ov["subtitle_placement"] == "above_speaker"
    finally:
        win.close()
        win.deleteLater()

    studio = ClipStudioWidget(settings_provider=lambda: settings)
    try:
        studio.apply_preset("ginux_clip_meme")
        cfg = studio.current_config()
        assert cfg.subtitles.placement_preset == "above_speaker"
        assert cfg.timing.silence_trim_enabled is True
        assert cfg.overlays.climax_effects.zoom_punch_repeat_sec == 3.5
        assert cfg.audio.contextual_sfx_enabled is True
        assert studio.sub_place_combo.currentData() == "above_speaker"
        assert studio.silence_trim_chk.isChecked() is True
        assert studio.zoom_repeat_spin.value() == 3.5
        assert studio.ctx_sfx_chk.isChecked() is True

        studio._add_sfx_cue_row()
        assert len(studio.current_config().audio.sfx_cues) == 1
        studio.sfx_cues_table.selectRow(0)
        studio._remove_sfx_cue_row()
        assert len(studio.current_config().audio.sfx_cues) == 0
    finally:
        studio.close()
        studio.deleteLater()

