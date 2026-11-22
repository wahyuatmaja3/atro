"""Shared 1:1 WYSIWYG Preview Renderer and FFmpeg Filtergraph Compiler for ATRO Composer."""

from __future__ import annotations

import asyncio
import math
import shutil
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from atro.core.composer_schema import (
    ClipComposerConfig,
    ClimaxEffectsConfig,
    CommentBubbleItem,
    ComposerLayoutMode,
    CropBoxConfig,
    DividerConfig,
    HookBannerConfig,
    LayoutLayerConfig,
    ShotCropSegment,
    WatermarkConfig,
)
from atro.core.schemas import TranscriptSegment
from atro.modules.cutter.framing.base import FramingContext
from atro.modules.cutter.framing.face_tracking import (
    FaceBox,
    ShotSegmentPlan,
    _analyze_shots_sync,
    _make_even,
)
from atro.modules.cutter.framing.game_layout import _detect_streamer_face_sync
from atro.modules.cutter.sfx import resolve_transition_sfx_path
from atro.modules.cutter.subtitle_burner import (
    build_composer_caption_chunks,
    compute_caption_line_layout,
    ensure_qt_gui_app,
    generate_composer_ass_file,
    is_action_token,
    qcolor_from_hex,
    register_qt_font,
    resolve_font_family,
    resolve_speaker_colors,
)
from atro.modules.cutter.transitions import (
    ResolvedShotTransition,
    active_transition_window_at,
    blend_transition_qimages,
    map_transition_to_ffmpeg_xfade,
    resolve_shot_transitions,
)


def compute_pixel_crop_rect(
    crop: CropBoxConfig,
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
) -> tuple[int, int, int, int]:
    """Convert normalized CropBoxConfig into aspect-matched even `(crop_w, crop_h, crop_x, crop_y)`."""
    sw = max(2, int(src_w) - (int(src_w) % 2))
    sh = max(2, int(src_h) - (int(src_h) % 2))
    target_ratio = max(0.05, float(target_w) / max(1.0, float(target_h)))

    box_w = max(8.0, float(crop.w) * sw)
    box_h = max(8.0, float(crop.h) * sh)
    cx = (float(crop.x) + float(crop.w) * 0.5) * sw
    cy = (float(crop.y) + float(crop.h) * 0.5) * sh

    if (box_w / box_h) > target_ratio:
        max_w = sh * target_ratio
        cw = _make_even(min(box_w, max_w), 2, sw)
        ch = _make_even(cw / target_ratio, 2, sh)
        cw = _make_even(min(cw, ch * target_ratio), 2, sw)
    else:
        max_h = sw / target_ratio
        ch = _make_even(min(box_h, max_h), 2, sh)
        cw = _make_even(ch * target_ratio, 2, sw)
        ch = _make_even(min(ch, cw / target_ratio), 2, sh)

    crop_x = max(0, min(sw - cw, int(round(cx - cw * 0.5))))
    crop_y = max(0, min(sh - ch, int(round(cy - ch * 0.5))))
    return cw, ch, crop_x, crop_y


@dataclass(slots=True)
class SplitGeometry:
    """Shared split-screen geometry for both straight and diagonal dividers."""

    split_y: int
    left_y: float
    right_y: float
    top_panel_h: int
    bot_panel_y: int
    bot_panel_h: int
    is_angled: bool


def compute_split_geometry(
    width: int,
    height: int,
    split_ratio_top: float,
    angle_deg: float,
) -> SplitGeometry:
    """Compute top/bottom panel heights and endpoints of a straight or tilted split divider."""
    w = max(2, int(width))
    h = max(2, int(height))
    split_y = _make_even(h * max(0.20, min(0.80, float(split_ratio_top))), 100, h - 100)
    clamped_angle = max(-25.0, min(25.0, float(angle_deg)))
    is_angled = abs(clamped_angle) >= 0.1

    if not is_angled:
        return SplitGeometry(
            split_y=split_y,
            left_y=float(split_y),
            right_y=float(split_y),
            top_panel_h=split_y,
            bot_panel_y=split_y,
            bot_panel_h=h - split_y,
            is_angled=False,
        )

    slope = math.tan(math.radians(clamped_angle))
    half_w = w * 0.5
    left_y = max(40.0, min(float(h - 40), split_y - half_w * slope))
    right_y = max(40.0, min(float(h - 40), split_y + half_w * slope))

    max_y = int(math.ceil(max(left_y, right_y))) + 4
    min_y = int(math.floor(min(left_y, right_y))) - 4

    top_panel_h = _make_even(min(h, max(120, max_y)), 120, h)
    bot_panel_y = max(0, min(h - 120, min_y))
    bot_panel_y -= bot_panel_y % 2
    bot_panel_h = _make_even(h - bot_panel_y, 120, h)

    return SplitGeometry(
        split_y=split_y,
        left_y=left_y,
        right_y=right_y,
        top_panel_h=top_panel_h,
        bot_panel_y=bot_panel_y,
        bot_panel_h=bot_panel_h,
        is_angled=True,
    )


def _shot_to_crop_boxes(
    shot: ShotSegmentPlan,
    mode: ComposerLayoutMode,
    angle_idx: int = 1,
) -> tuple[CropBoxConfig, CropBoxConfig]:
    """Convert one detected `ShotSegmentPlan` into normalized top & bottom CropBoxConfig."""
    if mode == "multi_speaker_split" and len(shot.centers) >= 2:
        (cx1, cy1), (cx2, cy2) = sorted(shot.centers[:2], key=lambda c: c[0])
        top_box = CropBoxConfig(
            auto_detect=True,
            x=max(0.0, min(0.65, cx1 - 0.18)),
            y=max(0.0, min(0.50, cy1 - 0.25)),
            w=0.36,
            h=0.65,
            label=f"Speaker Kiri (#{angle_idx})",
        )
        bot_box = CropBoxConfig(
            auto_detect=True,
            x=max(0.0, min(0.65, cx2 - 0.18)),
            y=max(0.0, min(0.50, cy2 - 0.25)),
            w=0.36,
            h=0.65,
            label=f"Speaker Kanan (#{angle_idx})",
        )
        return top_box, bot_box

    crop_w = 0.34 if mode == "single_focus" else 0.72
    crop_h = 1.0 if mode == "single_focus" else 0.96
    cx, cy = shot.centers[0] if shot.centers else (0.5, 0.42)
    tx = max(0.0, min(1.0 - crop_w, cx - crop_w * 0.5))
    ty = max(0.0, min(1.0 - crop_h, cy - crop_h * 0.42))
    return (
        CropBoxConfig(
            auto_detect=True,
            x=round(tx, 4),
            y=round(ty, 4),
            w=crop_w,
            h=crop_h,
            label=f"Speaker #{angle_idx}",
        ),
        CropBoxConfig(
            auto_detect=True,
            x=0.22,
            y=0.0,
            w=0.56,
            h=1.0,
            label="Panel Bawah",
        ),
    )


def detect_composer_shot_crops_sync(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    mode: ComposerLayoutMode,
    src_w: int = 1920,
    src_h: int = 1080,
    fps: float = 30.0,
    layout: Optional[LayoutLayerConfig] = None,
    job_dir: Optional[Path] = None,
    focus_hints: Optional[list] = None,
    transcript_segments: Optional[list[TranscriptSegment]] = None,
) -> list[ShotCropSegment]:
    """Detect all camera angles, speakers, and POI face crops across `[start_sec, end_sec]`."""
    duration = max(0.5, round(float(end_sec) - float(start_sec), 3))
    ctx = FramingContext(
        source_path=source_path,
        source_width=max(2, src_w),
        source_height=max(2, src_h),
        fps=max(1.0, fps),
        start_sec=max(0.0, start_sec),
        end_sec=max(start_sec + 0.5, end_sec),
        aspect_ratio="9:16",
    )

    if mode in ("split_screen", "pip_facecam"):
        face: Optional[FaceBox] = _detect_streamer_face_sync(ctx)
        if face is not None:
            cam_h = max(0.26, min(0.50, face.h * 2.8))
            cam_w = max(0.24, min(0.55, cam_h * (src_h / max(1, src_w)) * 1.35))
            tx = max(0.0, min(1.0 - cam_w, face.cx - cam_w * 0.5))
            ty = max(0.0, min(1.0 - cam_h, face.cy - cam_h * 0.38))
            top_box = CropBoxConfig(
                auto_detect=True,
                x=round(tx, 4),
                y=round(ty, 4),
                w=round(cam_w, 4),
                h=round(cam_h, 4),
                label="Facecam / Subjek",
            )
            bot_box = CropBoxConfig(
                auto_detect=True,
                x=0.22,
                y=0.0,
                w=0.56,
                h=1.0,
                label="Gameplay / Layar",
            )
            return [
                ShotCropSegment(
                    t_start=0.0,
                    t_end=duration,
                    top_crop=top_box,
                    bottom_crop=bot_box,
                    shot_mode="single_focus",
                    reason="speaker_match",
                    confidence=0.85,
                )
            ]

    # V2 Podcast / Multi-Angle / Multi-Speaker ShotPlanner (diarization + SFace + POI)
    try:
        from atro.modules.cutter.framing.shot_planner import plan_podcast_shots_sync

        eff_layout = layout or LayoutLayerConfig(mode=mode)
        v2_shots = plan_podcast_shots_sync(
            source_path=source_path,
            start_sec=start_sec,
            end_sec=end_sec,
            layout=eff_layout,
            src_w=src_w,
            src_h=src_h,
            fps=fps,
            job_dir=job_dir,
            focus_hints=focus_hints,
            transcript_segments=transcript_segments,
            mode_override=mode,
        )
        if v2_shots:
            return v2_shots
    except Exception:
        pass

    shots = _analyze_shots_sync(ctx)
    if not shots:
        face = _detect_streamer_face_sync(ctx)
        if face is not None:
            crop_w = 0.34 if mode == "single_focus" else 0.72
            crop_h = 1.0 if mode == "single_focus" else 0.96
            tx = max(0.0, min(1.0 - crop_w, face.cx - crop_w * 0.5))
            ty = max(0.0, min(1.0 - crop_h, face.cy - crop_h * 0.42))
            top_box = CropBoxConfig(
                auto_detect=True,
                x=round(tx, 4),
                y=round(ty, 4),
                w=crop_w,
                h=crop_h,
                label="Subjek Utama",
            )
        else:
            top_box = CropBoxConfig(
                auto_detect=False, x=0.33, y=0.05, w=0.34, h=0.90, label="Subjek Utama"
            )
        bot_box = CropBoxConfig(auto_detect=False, x=0.20, y=0.0, w=0.60, h=1.0, label="Panel Bawah")
        return [
            ShotCropSegment(
                t_start=0.0,
                t_end=duration,
                top_crop=top_box,
                bottom_crop=bot_box,
                shot_mode="single_focus",
            )
        ]

    segments: list[ShotCropSegment] = []
    seg_shot_mode = "multi_speaker_split" if mode == "multi_speaker_split" else (
        "blurred_frame" if mode == "blurred_frame" else "single_focus"
    )
    for idx, shot in enumerate(shots, start=1):
        top_box, bot_box = _shot_to_crop_boxes(shot, mode=mode, angle_idx=idx)
        segments.append(
            ShotCropSegment(
                t_start=round(max(0.0, shot.t_start), 3),
                t_end=round(max(shot.t_start + 0.1, shot.t_end), 3),
                top_crop=top_box,
                bottom_crop=bot_box if mode == "multi_speaker_split" else None,
                shot_mode=seg_shot_mode,  # type: ignore[arg-type]
            )
        )
    if segments:
        segments[0].t_start = 0.0
        segments[-1].t_end = duration
    return segments


def apply_auto_detect_to_layout_sync(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    layout: LayoutLayerConfig,
    src_w: int = 1920,
    src_h: int = 1080,
    fps: float = 30.0,
    job_dir: Optional[Path] = None,
    focus_hints: Optional[list] = None,
    transcript_segments: Optional[list[TranscriptSegment]] = None,
) -> list[ShotCropSegment]:
    """Detect all angle/face crops in `[start_sec, end_sec]` and populate `layout`."""
    segs = detect_composer_shot_crops_sync(
        source_path=source_path,
        start_sec=start_sec,
        end_sec=end_sec,
        mode=layout.mode,
        src_w=src_w,
        src_h=src_h,
        fps=fps,
        layout=layout,
        job_dir=job_dir,
        focus_hints=focus_hints,
        transcript_segments=transcript_segments,
    )
    if segs:
        layout.shot_crops = segs
        if layout.top_crop.auto_detect:
            layout.top_crop = segs[0].top_crop.model_copy(deep=True)
        if layout.bottom_crop.auto_detect and segs[0].bottom_crop is not None:
            layout.bottom_crop = segs[0].bottom_crop.model_copy(deep=True)
    return segs


async def apply_auto_detect_to_layout(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    layout: LayoutLayerConfig,
    src_w: int = 1920,
    src_h: int = 1080,
    fps: float = 30.0,
    job_dir: Optional[Path] = None,
    focus_hints: Optional[list] = None,
    transcript_segments: Optional[list[TranscriptSegment]] = None,
) -> list[ShotCropSegment]:
    """Async wrapper around apply_auto_detect_to_layout_sync."""
    return await asyncio.to_thread(
        apply_auto_detect_to_layout_sync,
        source_path,
        start_sec,
        end_sec,
        layout,
        src_w,
        src_h,
        fps,
        job_dir,
        focus_hints,
        transcript_segments,
    )


def auto_detect_composer_crops_sync(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    mode: ComposerLayoutMode,
    src_w: int = 1920,
    src_h: int = 1080,
    fps: float = 30.0,
) -> tuple[CropBoxConfig, CropBoxConfig]:
    """Detect subject face(s) in `[start_sec, end_sec]` and return top/bottom CropBoxConfig."""
    segs = detect_composer_shot_crops_sync(
        source_path=source_path,
        start_sec=start_sec,
        end_sec=end_sec,
        mode=mode,
        src_w=src_w,
        src_h=src_h,
        fps=fps,
    )
    if segs:
        top_box = segs[0].top_crop
        bot_box = (
            segs[0].bottom_crop
            if segs[0].bottom_crop is not None
            else CropBoxConfig(auto_detect=True, x=0.20, y=0.0, w=0.60, h=1.0, label="Panel Bawah")
        )
        return top_box, bot_box
    return (
        CropBoxConfig(auto_detect=False, x=0.33, y=0.05, w=0.34, h=0.90, label="Subjek Utama"),
        CropBoxConfig(auto_detect=False, x=0.20, y=0.0, w=0.60, h=1.0, label="Panel Bawah"),
    )


async def auto_detect_composer_crops(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    mode: ComposerLayoutMode,
    src_w: int = 1920,
    src_h: int = 1080,
    fps: float = 30.0,
) -> tuple[CropBoxConfig, CropBoxConfig]:
    """Async wrapper around auto_detect_composer_crops_sync."""
    return await asyncio.to_thread(
        auto_detect_composer_crops_sync,
        source_path,
        start_sec,
        end_sec,
        mode,
        src_w,
        src_h,
        fps,
    )


def draw_divider_on_painter(
    painter: QPainter,
    width: int,
    height: int,
    split_ratio_top: float,
    divider: DividerConfig,
) -> None:
    """Draw straight or angled split-screen divider with optional border and glow."""
    if not divider.enabled or divider.thickness_px <= 0:
        return
    geom = compute_split_geometry(width, height, split_ratio_top, divider.angle_deg)
    p1 = QPointF(0.0, geom.left_y)
    p2 = QPointF(float(width), geom.right_y)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    main_col = qcolor_from_hex(divider.color_hex, "#38BDF8")
    border_col = qcolor_from_hex(divider.border_color_hex, "#0B0F19")

    if divider.glow_enabled:
        glow_col = QColor(main_col)
        glow_col.setAlpha(65)
        glow_pen = QPen(glow_col, float(divider.thickness_px + 14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
        painter.setPen(glow_pen)
        painter.drawLine(p1, p2)

    border_pen = QPen(
        border_col,
        float(divider.thickness_px + 4),
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.FlatCap,
    )
    painter.setPen(border_pen)
    painter.drawLine(p1, p2)

    main_pen = QPen(
        main_col,
        float(divider.thickness_px),
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.FlatCap,
    )
    painter.setPen(main_pen)
    painter.drawLine(p1, p2)
    painter.restore()


def _hook_card_dimensions(hook: HookBannerConfig, canvas_w: int) -> tuple[int, int, list[str], QFont]:
    raw_text = (
        (hook.text or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\\n", "\n")
        .strip()
    )
    if not raw_text:
        empty_font = QFont("Segoe UI", max(14, int(hook.font_size)))
        empty_font.setBold(True)
        return 0, 0, [], empty_font

    max_text_w = max(220, canvas_w - 180)

    def _measure(pt_size: int) -> tuple[list[str], QFont, QFontMetrics, int]:
        f = QFont("Segoe UI", pt_size)
        f.setBold(True)
        metrics = QFontMetrics(f)
        wrapped: list[str] = []
        for para in raw_text.split("\n"):
            words = para.strip().split()
            if not words:
                continue
            cur = ""
            for w in words:
                cand = f"{cur} {w}".strip()
                if metrics.horizontalAdvance(cand) <= max_text_w or not cur:
                    cur = cand
                else:
                    wrapped.append(cur)
                    cur = w
            if cur:
                wrapped.append(cur)
        lines_out = wrapped[:5]
        max_w = max((metrics.horizontalAdvance(ln) for ln in lines_out), default=0)
        return lines_out, f, metrics, max_w

    cur_pt = max(14, int(hook.font_size))
    lines, font, fm, max_line_w = _measure(cur_pt)
    while lines and max_line_w > max_text_w and cur_pt > 14:
        cur_pt = max(14, cur_pt - 2)
        lines, font, fm, max_line_w = _measure(cur_pt)

    if not lines:
        return 0, 0, [], font
    card_w = min(canvas_w - 80, max(280, max_line_w + 68))
    line_h = fm.lineSpacing()
    card_h = max(76, len(lines) * line_h + 36)
    return card_w, card_h, lines, font


def render_hook_banner_card_image(
    hook: HookBannerConfig,
    canvas_w: int = 1080,
) -> Optional[tuple[QImage, int, int]]:
    """Render tight RGBA card QImage for Podcast Hook Banner and return `(image, target_x, target_y)`."""
    ensure_qt_gui_app()
    if not hook.enabled or not (hook.text or "").strip():
        return None
    card_w, card_h, lines, font = _hook_card_dimensions(hook, canvas_w)
    if not lines:
        return None

    img = QImage(card_w, card_h, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    bg_col = qcolor_from_hex(hook.bg_color_hex, "#E617110F")
    accent_col = qcolor_from_hex(hook.accent_color_hex, "#FACC15")
    txt_col = qcolor_from_hex(hook.text_color_hex, "#FFFFFF")

    rect = QRectF(2.0, 2.0, float(card_w - 4), float(card_h - 4))
    p.setPen(QPen(accent_col, 3.0))
    p.setBrush(bg_col)
    p.drawRoundedRect(rect, 22.0, 22.0)

    # Top accent pill bar
    bar_w = min(140.0, card_w * 0.32)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(accent_col)
    p.drawRoundedRect(QRectF((card_w - bar_w) * 0.5, 10.0, bar_w, 6.0), 3.0, 3.0)

    p.setFont(font)
    p.setPen(txt_col)
    text_rect = QRectF(28.0, 24.0, float(card_w - 56), float(card_h - 34))
    p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "\n".join(lines))
    p.end()

    target_x = max(0, (canvas_w - card_w) // 2)
    target_y = max(12, int(hook.pos_y) - card_h // 2)
    return img, target_x, target_y


def draw_hook_banner_on_painter(
    painter: QPainter,
    hook: HookBannerConfig,
    canvas_w: int,
    canvas_h: int,
    rel_t: float,
) -> None:
    """Draw animated Hook Banner onto active QPainter at `rel_t`."""
    if not hook.enabled or rel_t < 0.0 or rel_t > hook.duration_sec:
        return
    card_data = render_hook_banner_card_image(hook, canvas_w=canvas_w)
    if card_data is None:
        return
    img, target_x, target_y = card_data
    target_y = min(canvas_h - img.height() - 12, target_y)

    dur = max(0.3, float(hook.duration_sec))
    in_dur = min(0.22, dur * 0.35)
    out_dur = min(0.22, dur * 0.35)

    alpha = 1.0
    dx = 0.0
    dy = 0.0
    scale = 1.0

    if rel_t < in_dur:
        prog = max(0.0, min(1.0, rel_t / max(0.01, in_dur)))
        if hook.transition_in == "fade":
            alpha = prog
        elif hook.transition_in == "slide":
            alpha = prog
            dy = -(1.0 - prog) * 180.0
        elif hook.transition_in == "zoom":
            alpha = prog
            scale = 0.72 + 0.28 * prog
        elif hook.transition_in == "whip":
            dx = -(1.0 - prog) * float(canvas_w)
    elif rel_t > dur - out_dur:
        prog = max(0.0, min(1.0, (dur - rel_t) / max(0.01, out_dur)))
        if hook.transition_out == "fade":
            alpha = prog
        elif hook.transition_out == "slide":
            alpha = prog
            dy = -(1.0 - prog) * 180.0
        elif hook.transition_out == "zoom":
            alpha = prog
            scale = 0.78 + 0.22 * prog
        elif hook.transition_out == "whip":
            dx = (1.0 - prog) * float(canvas_w)

    painter.save()
    painter.setOpacity(max(0.0, min(1.0, alpha)))
    cx = target_x + img.width() * 0.5 + dx
    cy = target_y + img.height() * 0.5 + dy
    painter.translate(cx, cy)
    if abs(scale - 1.0) > 1e-3:
        painter.scale(scale, scale)
    painter.drawImage(QPointF(-img.width() * 0.5, -img.height() * 0.5), img)
    painter.restore()


def render_comment_bubble_card_image(
    bubble: CommentBubbleItem,
    canvas_w: int = 1080,
    canvas_h: int = 1920,
) -> tuple[QImage, int, int]:
    """Render Twitch/Stream style Comment Bubble into tight RGBA QImage + `(x, y)`."""
    ensure_qt_gui_app()
    sc = max(0.5, min(2.0, float(bubble.scale)))
    card_w = int(round(min(canvas_w - 64, 780 * sc)))
    body_font = QFont("Segoe UI", max(16, int(round(32 * sc))))
    body_font.setBold(True)
    header_font = QFont("Segoe UI", max(13, int(round(24 * sc))))
    header_font.setBold(True)

    fm_body = QFontMetrics(body_font)
    avail_text_w = max(220, card_w - int(150 * sc))
    words = (bubble.text or "").strip().split()
    wrapped_lines: list[str] = []
    cur_line = ""
    for w in words:
        cand = f"{cur_line} {w}".strip()
        if fm_body.horizontalAdvance(cand) <= avail_text_w or not cur_line:
            cur_line = cand
        else:
            wrapped_lines.append(cur_line)
            cur_line = w
    if cur_line:
        wrapped_lines.append(cur_line)
    if not wrapped_lines:
        wrapped_lines = ["..."]

    header_h = int(round(34 * sc))
    body_h = len(wrapped_lines) * fm_body.lineSpacing()
    card_h = max(int(round(112 * sc)), header_h + body_h + int(round(48 * sc)))

    img = QImage(card_w, card_h, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    bg_col = qcolor_from_hex(bubble.bg_color_hex, "#F218181B")
    av_col = qcolor_from_hex(bubble.avatar_color_hex, "#9146FF")
    txt_col = qcolor_from_hex(bubble.text_color_hex, "#F8FAFC")

    p.setPen(QPen(av_col, max(2.0, 3.0 * sc)))
    p.setBrush(bg_col)
    p.drawRoundedRect(QRectF(2.0, 2.0, card_w - 4.0, card_h - 4.0), 24.0 * sc, 24.0 * sc)

    # Circular avatar
    av_size = int(round(68 * sc))
    av_x = int(round(24 * sc))
    av_y = (card_h - av_size) // 2
    av_rect = QRectF(float(av_x), float(av_y), float(av_size), float(av_size))

    clip_path = QPainterPath()
    clip_path.addEllipse(av_rect)
    p.save()
    p.setClipPath(clip_path)
    drew_avatar = False
    if bubble.avatar_path and Path(bubble.avatar_path).is_file():
        av_img = QImage(bubble.avatar_path)
        if not av_img.isNull():
            p.drawImage(av_rect, av_img)
            drew_avatar = True
    if not drew_avatar:
        p.fillPath(clip_path, av_col)
        p.setPen(QColor("#FFFFFF"))
        av_font = QFont("Segoe UI", max(14, int(round(28 * sc))))
        av_font.setBold(True)
        p.setFont(av_font)
        initial = (bubble.username or "V").strip()[:1].upper() or "V"
        p.drawText(av_rect, Qt.AlignmentFlag.AlignCenter, initial)
    p.restore()

    # Label + username
    text_left = av_x + av_size + int(round(20 * sc))
    text_w = card_w - text_left - int(round(24 * sc))
    p.setFont(header_font)
    p.setPen(av_col)
    label_str = (bubble.author_label or "viewers:").strip()
    label_w = QFontMetrics(header_font).horizontalAdvance(label_str + " ")
    top_pad = int(round(20 * sc))
    p.drawText(QRectF(text_left, top_pad, label_w + 10, header_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label_str)

    p.setPen(QColor("#CBD5E1"))
    p.drawText(
        QRectF(text_left + label_w + 6, top_pad, max(40, text_w - label_w - 6), header_h),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        (bubble.username or "").strip(),
    )

    # Comment body
    p.setFont(body_font)
    p.setPen(txt_col)
    p.drawText(
        QRectF(text_left, top_pad + header_h + int(round(4 * sc)), text_w, body_h + 12),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        "\n".join(wrapped_lines),
    )
    p.end()

    x = max(8, min(canvas_w - card_w - 8, int(bubble.pos_x) - card_w // 2))
    y = max(8, min(canvas_h - card_h - 8, int(bubble.pos_y) - card_h // 2))
    return img, x, y


def draw_comment_bubble_on_painter(
    painter: QPainter,
    bubble: CommentBubbleItem,
    canvas_w: int,
    canvas_h: int,
    rel_t: float,
) -> None:
    """Draw a timed Viewer Comment Bubble onto QPainter if active at `rel_t`."""
    if not bubble.enabled or rel_t < bubble.start_sec or rel_t > bubble.end_sec:
        return
    img, x, y = render_comment_bubble_card_image(bubble, canvas_w=canvas_w, canvas_h=canvas_h)
    painter.save()
    painter.drawImage(QPointF(float(x), float(y)), img)
    painter.restore()


def render_watermark_card_image(
    wm: WatermarkConfig,
    canvas_w: int = 1080,
    canvas_h: int = 1920,
) -> Optional[tuple[QImage, int, int]]:
    """Render logo or text watermark into an RGBA QImage and return `(image, x, y)`."""
    ensure_qt_gui_app()
    if not wm.enabled:
        return None
    sc = max(0.25, min(3.0, float(wm.scale)))

    img: Optional[QImage] = None
    if wm.logo_path and Path(wm.logo_path).is_file():
        loaded = QImage(wm.logo_path)
        if not loaded.isNull():
            target_w = max(48, int(round(170 * sc)))
            scaled = loaded.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
            img = QImage(scaled.size(), QImage.Format.Format_RGBA8888)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img)
            p.setOpacity(max(0.05, min(1.0, float(wm.opacity))))
            p.drawImage(0, 0, scaled)
            p.end()

    if img is None:
        text = (wm.text or "").strip()
        if not text:
            return None
        font = QFont("Segoe UI", max(14, int(round(28 * sc))))
        font.setBold(True)
        fm = QFontMetrics(font)
        w = min(canvas_w - 40, fm.horizontalAdvance(text) + 36)
        h = fm.height() + 18
        img = QImage(w, h, QImage.Format.Format_RGBA8888)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(max(0.05, min(1.0, float(wm.opacity))))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(15, 17, 23, 150))
        p.drawRoundedRect(QRectF(0, 0, w, h), 10.0, 10.0)
        p.setFont(font)
        p.setPen(QColor("#F8FAFC"))
        p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text)
        p.end()

    margin = 36
    iw, ih = img.width(), img.height()
    if wm.position == "top_left":
        x, y = margin, margin
    elif wm.position == "top_right":
        x, y = canvas_w - iw - margin, margin
    elif wm.position == "bottom_left":
        x, y = margin, canvas_h - ih - margin - 40
    elif wm.position == "bottom_right":
        x, y = canvas_w - iw - margin, canvas_h - ih - margin - 40
    elif wm.position == "center_split":
        x, y = (canvas_w - iw) // 2, (canvas_h - ih) // 2
    else:
        x = max(8, min(canvas_w - iw - 8, int(wm.pos_x) - iw // 2))
        y = max(8, min(canvas_h - ih - 8, int(wm.pos_y) - ih // 2))
    return img, x, y


def draw_watermark_on_painter(
    painter: QPainter,
    wm: WatermarkConfig,
    canvas_w: int,
    canvas_h: int,
) -> None:
    """Draw watermark onto QPainter."""
    card = render_watermark_card_image(wm, canvas_w=canvas_w, canvas_h=canvas_h)
    if card is None:
        return
    img, x, y = card
    painter.save()
    painter.drawImage(QPointF(float(x), float(y)), img)
    painter.restore()


def render_topic_header_card_image(
    climax: ClimaxEffectsConfig,
    canvas_w: int = 1080,
) -> Optional[tuple[QImage, int, int]]:
    """Render a persistent top topic header pill for OpusClip/Subathon style overlays."""
    text = (climax.topic_header_text or "").strip()
    if not climax.topic_header_enabled or not text:
        return None
    ensure_qt_gui_app()

    font = QFont("Segoe UI", 30)
    font.setBold(True)
    fm = QFontMetrics(font)
    max_text_w = max(240, canvas_w - 220)
    wrapped = textwrap.wrap(text.upper(), width=34)[:2]
    longest_w = max((fm.horizontalAdvance(line) for line in wrapped), default=200)
    pill_w = min(canvas_w - 110, max(260, longest_w + 68))
    pill_h = len(wrapped) * fm.height() + 26

    img = QImage(pill_w, pill_h, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(2.0, 2.0, float(pill_w - 4), float(pill_h - 4))
    p.setPen(QPen(QColor("#F97316"), 2.5))
    p.setBrush(QColor(18, 16, 22, 228))
    p.drawRoundedRect(rect, float(pill_h) * 0.48, float(pill_h) * 0.48)

    p.setFont(font)
    p.setPen(QColor("#F8FAFC"))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "\n".join(wrapped))
    p.end()

    x = (canvas_w - pill_w) // 2
    y = 58
    return img, x, y


def draw_topic_header_on_painter(
    painter: QPainter,
    climax: ClimaxEffectsConfig,
    canvas_w: int,
) -> None:
    """Draw persistent top topic header badge onto QPainter."""
    card = render_topic_header_card_image(climax, canvas_w=canvas_w)
    if card is None:
        return
    img, x, y = card
    painter.save()
    painter.drawImage(QPointF(float(x), float(y)), img)
    painter.restore()


def _draw_vignette_on_painter(painter: QPainter, canvas_w: int, canvas_h: int) -> None:
    """Draw subtle radial edge darkening matching FFmpeg vignette=angle=PI/5."""
    cx = float(canvas_w) * 0.5
    cy = float(canvas_h) * 0.5
    radius = math.hypot(cx, cy)
    grad = QRadialGradient(QPointF(cx, cy), radius)
    grad.setColorAt(0.0, QColor(0, 0, 0, 0))
    grad.setColorAt(0.55, QColor(0, 0, 0, 12))
    grad.setColorAt(1.0, QColor(0, 0, 0, 130))
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawRect(QRectF(0.0, 0.0, float(canvas_w), float(canvas_h)))
    painter.restore()


def compute_pip_rect(
    canvas_w: int,
    canvas_h: int,
    layout: LayoutLayerConfig,
) -> tuple[int, int, int, int]:
    """Compute even `(x, y, w, h)` for floating PiP facecam overlay in canvas coordinates."""
    scale = max(0.18, min(0.55, float(layout.pip_scale)))
    pip_w = max(80, _make_even(canvas_w * scale, 2, canvas_w))
    if layout.pip_shape == "circle":
        pip_h = pip_w
    else:
        pip_h = max(80, _make_even(pip_w * 4.0 / 3.0, 2, canvas_h - 320))

    margin_x = 36
    margin_top = 152
    margin_bottom = 264
    pos = layout.pip_position
    px = margin_x if "left" in pos else (canvas_w - pip_w - margin_x)
    py = margin_top if "top" in pos else (canvas_h - pip_h - margin_bottom)
    px = max(0, min(canvas_w - pip_w, px))
    py = max(0, min(canvas_h - pip_h, py))
    px -= px % 2
    py -= py % 2
    return px, py, pip_w, pip_h


def _build_frame_clip_path(
    rect: QRectF,
    shape: str,
    corner_radius: float,
) -> QPainterPath:
    """Build circular or rounded-rect QPainterPath for PiP facecam and blurred frame cards."""
    path = QPainterPath()
    if shape == "circle":
        path.addEllipse(rect)
    else:
        r = max(0.0, min(float(corner_radius), rect.width() * 0.45, rect.height() * 0.45))
        if r >= 1.0:
            path.addRoundedRect(rect, r, r)
        else:
            path.addRect(rect)
    return path


def render_frame_shape_assets(
    width: int,
    height: int,
    shape: str,
    corner_radius_px: int,
    border_width_px: int,
    border_color_hex: str,
    out_dir: Path,
    prefix: str,
) -> tuple[Path, Optional[Path]]:
    """Generate grayscale alpha mask PNG + optional inset neon border PNG for FFmpeg."""
    ensure_qt_gui_app()
    mask_img = QImage(width, height, QImage.Format.Format_Grayscale8)
    mask_img.fill(Qt.GlobalColor.black)
    mp = QPainter(mask_img)
    mp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    mp.setPen(Qt.PenStyle.NoPen)
    mp.setBrush(Qt.GlobalColor.white)
    mp.drawPath(_build_frame_clip_path(QRectF(0.0, 0.0, float(width), float(height)), shape, corner_radius_px))
    mp.end()

    mask_path = out_dir / f"{prefix}_mask.png"
    mask_img.save(str(mask_path), "PNG")

    border_path: Optional[Path] = None
    bw = max(0, int(border_width_px))
    if bw > 0:
        border_img = QImage(width, height, QImage.Format.Format_RGBA8888)
        border_img.fill(Qt.GlobalColor.transparent)
        bp = QPainter(border_img)
        bp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inset = bw * 0.5
        stroke_rect = QRectF(inset, inset, max(2.0, float(width) - bw), max(2.0, float(height) - bw))
        stroke_path = _build_frame_clip_path(stroke_rect, shape, max(0.0, corner_radius_px - inset))
        bp.setPen(QPen(qcolor_from_hex(border_color_hex, "#F97316"), float(bw)))
        bp.setBrush(Qt.BrushStyle.NoBrush)
        bp.drawPath(stroke_path)
        bp.end()
        border_path = out_dir / f"{prefix}_border.png"
        border_img.save(str(border_path), "PNG")

    return mask_path, border_path


def _draw_stroked_word(
    painter: QPainter,
    x: float,
    baseline_y: float,
    text: str,
    font: QFont,
    fill_color: QColor,
    outline_color: QColor,
    outline_width: float,
    shadow_color: QColor,
    shadow_depth: float,
    scale: float = 1.0,
) -> None:
    path = QPainterPath()
    path.addText(0.0, 0.0, font, text)
    painter.save()
    fm = QFontMetrics(font)
    word_w = fm.horizontalAdvance(text)
    painter.translate(x + word_w * 0.5, baseline_y)
    if abs(scale - 1.0) > 1e-3:
        painter.scale(scale, scale)
    painter.translate(-word_w * 0.5, 0.0)

    if shadow_depth > 0.1:
        painter.save()
        painter.translate(shadow_depth, shadow_depth)
        painter.fillPath(path, shadow_color)
        painter.restore()

    if outline_width > 0.1:
        pen = QPen(outline_color, float(outline_width) * 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, pen)
    painter.fillPath(path, fill_color)
    painter.restore()


def draw_subtitles_on_painter(
    painter: QPainter,
    config: ClipComposerConfig,
    rel_t: float,
    segments: list[TranscriptSegment],
    canvas_w: int,
    canvas_h: int,
    clip_start_sec: Optional[float] = None,
    clip_end_sec: Optional[float] = None,
) -> None:
    """Render word-level dynamic captions + speaker colors + footnote at exact config coordinates."""
    subs = config.subtitles
    if not subs.enabled:
        return

    chunks = build_composer_caption_chunks(
        config,
        segments,
        clip_start_sec=clip_start_sec,
        clip_end_sec=clip_end_sec,
    )
    active_chunk = None
    for ch in chunks:
        if ch.start <= rel_t <= ch.end:
            active_chunk = ch
            break

    family = register_qt_font(subs.custom_font_path, subs.font_family)
    if active_chunk is not None:
        active_word_idx = -1
        if subs.style_mode != "plain":
            for idx, w in enumerate(active_chunk.words):
                next_start = (
                    active_chunk.words[idx + 1].start
                    if idx + 1 < len(active_chunk.words)
                    else active_chunk.end
                )
                if w.start <= rel_t <= next_start:
                    active_word_idx = idx
                    break
            if active_word_idx < 0:
                active_word_idx = len(active_chunk.words) - 1

        p_hex, h_hex, o_hex = resolve_speaker_colors(config, active_chunk.speaker)
        primary_col = qcolor_from_hex(p_hex, "#FFFFFF")
        highlight_col = qcolor_from_hex(h_hex, "#FACC15")
        outline_col = qcolor_from_hex(o_hex, "#000000")
        secondary_col = qcolor_from_hex(subs.secondary_keyword_color_hex, "#22C55E")
        action_col = qcolor_from_hex(subs.action_token_color_hex, "#F97316")
        shadow_col = qcolor_from_hex(subs.shadow_color_hex, "#80000000")

        layout = compute_caption_line_layout(
            config=config,
            raw_words=[w.text for w in active_chunk.words],
            active_idx=active_word_idx,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        if subs.caption_bg_mode == "full_line_box":
            pill_col = qcolor_from_hex(subs.active_word_bg_hex, "#CC111827")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pill_col)
            box_rect = QRectF(
                layout.box_center_x - layout.box_w * 0.5,
                layout.box_center_y - layout.box_h * 0.5,
                layout.box_w,
                layout.box_h,
            )
            r = min(float(subs.active_word_bg_radius), layout.box_w * 0.45, layout.box_h * 0.45)
            painter.drawRoundedRect(box_rect, r, r)
        elif subs.caption_bg_mode == "active_word_pill" and active_word_idx >= 0:
            pill_col = qcolor_from_hex(subs.active_word_bg_hex, "#F97316")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pill_col)
            pill_rect = QRectF(
                layout.pill_center_x - layout.pill_w * 0.5,
                layout.pill_center_y - layout.pill_h * 0.5,
                layout.pill_w,
                layout.pill_h,
            )
            r = min(float(subs.active_word_bg_radius), layout.pill_w * 0.45, layout.pill_h * 0.45)
            painter.drawRoundedRect(pill_rect, r, r)

        if subs.auto_emoji_enabled and layout.emoji:
            emoji_font = QFont("Segoe UI Emoji", max(20, int(round(subs.font_size * 0.90))))
            painter.save()
            painter.setFont(emoji_font)
            em_rect = QRectF(
                float(subs.pos_x) - 120.0,
                layout.emoji_y - 60.0,
                240.0,
                120.0,
            )
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(em_rect, Qt.AlignmentFlag.AlignCenter, layout.emoji)
            painter.restore()

        painter.translate(float(subs.pos_x), float(subs.pos_y))
        if layout.scale_fit < 0.999:
            painter.scale(layout.scale_fit, layout.scale_fit)

        for wb in layout.word_boxes:
            if wb.is_active:
                fill_c = action_col if wb.is_action else highlight_col
                w_scale = 1.12 if subs.animation == "pop" else (1.16 if subs.animation == "scale" else 1.0)
            else:
                if wb.is_action:
                    fill_c = action_col
                elif wb.is_secondary_keyword:
                    fill_c = secondary_col
                else:
                    fill_c = primary_col
                w_scale = 1.0

            _draw_stroked_word(
                painter=painter,
                x=wb.local_x,
                baseline_y=layout.baseline_y,
                text=wb.text,
                font=layout.font,
                fill_color=fill_c,
                outline_color=outline_col,
                outline_width=subs.outline_width,
                shadow_color=shadow_col,
                shadow_depth=subs.shadow_depth,
                scale=w_scale,
            )
        painter.restore()

    if subs.footnote.enabled and subs.footnote.text.strip():
        fn_font = QFont(family, max(12, int(subs.footnote.font_size)))
        fn_font.setBold(True)
        fn_y = min(canvas_h - 36, int(subs.pos_y) + int(subs.footnote.offset_y))
        fn_rect = QRectF(40.0, float(fn_y - 26), float(canvas_w - 80), 52.0)
        painter.save()
        painter.setFont(fn_font)
        painter.setPen(QColor(0, 0, 0, 210))
        painter.drawText(fn_rect.translated(2.0, 2.0), Qt.AlignmentFlag.AlignCenter, subs.footnote.text.strip())
        painter.setPen(qcolor_from_hex(subs.footnote.color_hex, "#CBD5E1"))
        painter.drawText(fn_rect, Qt.AlignmentFlag.AlignCenter, subs.footnote.text.strip())
        painter.restore()


def _crop_and_resize_bgr(
    frame_bgr: np.ndarray,
    crop: CropBoxConfig,
    target_w: int,
    target_h: int,
) -> np.ndarray:
    sh, sw = frame_bgr.shape[:2]
    cw, ch, cx, cy = compute_pixel_crop_rect(crop, sw, sh, target_w, target_h)
    sub = frame_bgr[cy : cy + ch, cx : cx + cw]
    if sub.size == 0:
        return cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_AREA)
    return cv2.resize(sub, (target_w, target_h), interpolation=cv2.INTER_LINEAR)


def _apply_climax_cv(
    canvas_bgr: np.ndarray,
    config: ClipComposerConfig,
    rel_t: float,
) -> np.ndarray:
    climax = config.overlays.climax_effects
    out = canvas_bgr
    if climax.contrast_boost_enabled:
        alpha = float(climax.contrast)
        beta = float(climax.brightness) * 255.0
        out = cv2.convertScaleAbs(out, alpha=alpha, beta=beta)
        if abs(float(climax.saturation) - 1.0) > 0.02:
            hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * float(climax.saturation), 0, 255)
            out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    if climax.zoom_punch_enabled:
        zs = float(climax.zoom_punch_start_sec)
        ze = zs + float(climax.zoom_punch_duration_sec)
        if zs <= rel_t <= ze:
            zf = max(1.01, float(climax.zoom_punch_factor))
            h, w = out.shape[:2]
            cw = max(2, int(round(w / zf)))
            ch = max(2, int(round(h / zf)))
            cx = max(0, (w - cw) // 2)
            cy = max(0, (h - ch) // 2)
            out = cv2.resize(out[cy : cy + ch, cx : cx + cw], (w, h), interpolation=cv2.INTER_LINEAR)
    return out


def _bgr_to_qimage(frame_bgr: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    return QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()


def _render_base_canvas_qimage(
    source_frame_bgr: np.ndarray,
    config: ClipComposerConfig,
    rel_time_sec: float,
    cw: int,
    ch: int,
    active_top_crop: CropBoxConfig,
    active_bot_crop: CropBoxConfig,
) -> QImage:
    """Render video layout layers (`single_focus`, `blurred_frame`, `pip_facecam`, `split`) into RGBA8888 QImage."""
    layout = config.layout
    mode = layout.mode

    if config.aspect_ratio == "16:9" or mode == "single_focus":
        base_bgr = _crop_and_resize_bgr(source_frame_bgr, active_top_crop, cw, ch)
        base_bgr = _apply_climax_cv(base_bgr, config, rel_time_sec)
        return _bgr_to_qimage(base_bgr).convertToFormat(QImage.Format.Format_RGBA8888)

    if mode == "blurred_frame":
        bg_bgr = _crop_and_resize_bgr(source_frame_bgr, active_bot_crop, cw, ch)
        k = max(3, int(layout.background_blur_strength) * 2 + 1)
        small_bg = cv2.resize(bg_bgr, (cw // 4, ch // 4), interpolation=cv2.INTER_AREA)
        small_bg = cv2.GaussianBlur(small_bg, (k // 2 | 1, k // 2 | 1), 0)
        bg_bgr = cv2.resize(small_bg, (cw, ch), interpolation=cv2.INTER_LINEAR)
        darken_beta = float(layout.background_darken) * 255.0
        bg_bgr = cv2.convertScaleAbs(bg_bgr, alpha=1.0, beta=darken_beta)

        fg_ratio_map = {"1:1": (cw, cw), "4:3": (cw, int(round(cw * 0.75))), "16:9": (cw, int(round(cw * 9.0 / 16.0)))}
        fg_w, fg_h = fg_ratio_map.get(layout.blurred_frame_aspect, (cw, int(round(cw * 0.75))))
        fg_h = min(ch, _make_even(fg_h, 2, ch))
        fg_bgr = _crop_and_resize_bgr(source_frame_bgr, active_top_crop, fg_w, fg_h)
        y_off = max(0, (ch - fg_h) // 2)

        bg_bgr = _apply_climax_cv(bg_bgr, config, rel_time_sec)
        fg_bgr = _apply_climax_cv(fg_bgr, config, rel_time_sec)
        canvas_img = _bgr_to_qimage(bg_bgr).convertToFormat(QImage.Format.Format_RGBA8888)
        fg_qimg = _bgr_to_qimage(fg_bgr)
        p = QPainter(canvas_img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fg_rect = QRectF(0.0, float(y_off), float(fg_w), float(fg_h))
        clip_path = _build_frame_clip_path(fg_rect, "rounded_rect", layout.frame_corner_radius_px)
        p.save()
        p.setClipPath(clip_path)
        p.drawImage(0, y_off, fg_qimg)
        p.restore()
        if layout.frame_border_width_px > 0:
            bw = float(layout.frame_border_width_px)
            inset = bw * 0.5
            stroke_rect = QRectF(inset, float(y_off) + inset, max(2.0, float(fg_w) - bw), max(2.0, float(fg_h) - bw))
            stroke_path = _build_frame_clip_path(stroke_rect, "rounded_rect", max(0.0, layout.frame_corner_radius_px - inset))
            p.save()
            p.setPen(QPen(qcolor_from_hex(layout.frame_border_color_hex, "#F97316"), bw))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(stroke_path)
            p.restore()
        p.end()
        return canvas_img

    if mode == "pip_facecam":
        bg_bgr = _crop_and_resize_bgr(source_frame_bgr, active_bot_crop, cw, ch)
        bg_bgr = _apply_climax_cv(bg_bgr, config, rel_time_sec)
        canvas_img = _bgr_to_qimage(bg_bgr).convertToFormat(QImage.Format.Format_RGBA8888)

        px, py, pip_w, pip_h = compute_pip_rect(cw, ch, layout)
        pip_bgr = _crop_and_resize_bgr(source_frame_bgr, active_top_crop, pip_w, pip_h)
        pip_bgr = _apply_climax_cv(pip_bgr, config, rel_time_sec)
        pip_qimg = _bgr_to_qimage(pip_bgr)

        p = QPainter(canvas_img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pip_rect = QRectF(float(px), float(py), float(pip_w), float(pip_h))
        pip_path = _build_frame_clip_path(pip_rect, layout.pip_shape, layout.frame_corner_radius_px)
        p.save()
        p.setClipPath(pip_path)
        p.drawImage(px, py, pip_qimg)
        p.restore()
        if layout.frame_border_width_px > 0:
            bw = float(layout.frame_border_width_px)
            inset = bw * 0.5
            stroke_rect = QRectF(float(px) + inset, float(py) + inset, max(2.0, float(pip_w) - bw), max(2.0, float(pip_h) - bw))
            stroke_path = _build_frame_clip_path(stroke_rect, layout.pip_shape, max(0.0, layout.frame_corner_radius_px - inset))
            p.save()
            p.setPen(QPen(qcolor_from_hex(layout.frame_border_color_hex, "#F97316"), bw))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(stroke_path)
            p.restore()
        p.end()
        return canvas_img

    # split_screen or multi_speaker_split
    geom = compute_split_geometry(cw, ch, layout.split_ratio_top, layout.divider.angle_deg)
    top_bgr = _crop_and_resize_bgr(source_frame_bgr, active_top_crop, cw, geom.top_panel_h)
    bot_bgr = _crop_and_resize_bgr(source_frame_bgr, active_bot_crop, cw, geom.bot_panel_h)

    if not geom.is_angled:
        combined = np.vstack([top_bgr, bot_bgr])
        combined = _apply_climax_cv(combined, config, rel_time_sec)
        canvas_img = _bgr_to_qimage(combined).convertToFormat(QImage.Format.Format_RGBA8888)
        p = QPainter(canvas_img)
    else:
        bot_full = np.zeros((ch, cw, 3), dtype=np.uint8)
        bot_full[geom.bot_panel_y : geom.bot_panel_y + geom.bot_panel_h, 0:cw] = bot_bgr
        bot_full = _apply_climax_cv(bot_full, config, rel_time_sec)
        canvas_img = _bgr_to_qimage(bot_full).convertToFormat(QImage.Format.Format_RGBA8888)

        top_bgr = _apply_climax_cv(top_bgr, config, rel_time_sec)
        top_qimg = _bgr_to_qimage(top_bgr)

        p = QPainter(canvas_img)
        poly = QPolygonF(
            [
                QPointF(0.0, 0.0),
                QPointF(float(cw), 0.0),
                QPointF(float(cw), geom.right_y),
                QPointF(0.0, geom.left_y),
            ]
        )
        clip_path = QPainterPath()
        clip_path.addPolygon(poly)
        p.save()
        p.setClipPath(clip_path)
        p.drawImage(0, 0, top_qimg)
        p.restore()

    draw_divider_on_painter(p, cw, ch, layout.split_ratio_top, layout.divider)
    p.end()
    return canvas_img


def render_preview_frame(
    source_frame_bgr: Optional[np.ndarray],
    config: ClipComposerConfig,
    rel_time_sec: float,
    segments: list[TranscriptSegment],
    clip_start_sec: Optional[float] = None,
    clip_end_sec: Optional[float] = None,
    prev_source_frame_bgr: Optional[np.ndarray] = None,
) -> QImage:
    """Render a 1:1 WYSIWYG 1080x1920 (or 1920x1080) QImage preview frame from ClipComposerConfig."""
    ensure_qt_gui_app()
    cw = max(320, int(config.canvas_width))
    ch = max(320, int(config.canvas_height))
    pad = max(0.0, float(config.timing.padding_sec))
    eff_start = clip_start_sec if clip_start_sec is not None else max(0.0, config.timing.start_time - pad)
    eff_end = clip_end_sec if clip_end_sec is not None else (config.timing.end_time + pad)
    eff_dur = max(0.5, round(eff_end - eff_start, 3))

    if source_frame_bgr is None or source_frame_bgr.size == 0:
        # Fallback dark slate placeholder if no video frame is loaded
        placeholder = np.full((ch, cw, 3), (23, 17, 15), dtype=np.uint8)
        canvas_img = _bgr_to_qimage(placeholder).convertToFormat(QImage.Format.Format_RGBA8888)
        painter = QPainter(canvas_img)
    else:
        layout = config.layout
        tr_win = (
            active_transition_window_at(layout.shot_crops, layout.transition, rel_time_sec)
            if (layout.framing_tracking_mode == "auto_face" and len(layout.shot_crops) > 1 and config.aspect_ratio != "16:9")
            else None
        )
        if tr_win is not None:
            idx_a, idx_b, tr_res, prog = tr_win
            seg_a = layout.shot_crops[idx_a]
            seg_b = layout.shot_crops[idx_b]
            frame_a = (
                prev_source_frame_bgr
                if (prev_source_frame_bgr is not None and prev_source_frame_bgr.size > 0)
                else source_frame_bgr
            )
            img_a = _render_base_canvas_qimage(
                frame_a,
                config,
                rel_time_sec,
                cw,
                ch,
                seg_a.top_crop,
                seg_a.bottom_crop or layout.bottom_crop,
            )
            img_b = _render_base_canvas_qimage(
                source_frame_bgr,
                config,
                rel_time_sec,
                cw,
                ch,
                seg_b.top_crop,
                seg_b.bottom_crop or layout.bottom_crop,
            )
            canvas_img = blend_transition_qimages(img_a, img_b, prog, tr_res.tr_type, tr_res.easing)
        else:
            active_top_crop, active_bot_crop = layout.resolve_crops_at_time(rel_time_sec)
            canvas_img = _render_base_canvas_qimage(
                source_frame_bgr,
                config,
                rel_time_sec,
                cw,
                ch,
                active_top_crop,
                active_bot_crop,
            )
        painter = QPainter(canvas_img)

    # Shared Overlay Stack (1:1 coordinates)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    climax = config.overlays.climax_effects
    if climax.vignette_enabled:
        _draw_vignette_on_painter(painter, cw, ch)
    draw_topic_header_on_painter(painter, climax, cw)
    draw_hook_banner_on_painter(painter, config.overlays.hook_banner, cw, ch, rel_time_sec)
    for bubble in config.overlays.comment_bubbles:
        draw_comment_bubble_on_painter(painter, bubble, cw, ch, rel_time_sec)
    draw_watermark_on_painter(painter, config.overlays.watermark, cw, ch)
    draw_subtitles_on_painter(
        painter,
        config,
        rel_time_sec,
        segments,
        cw,
        ch,
        clip_start_sec=eff_start,
        clip_end_sec=eff_end,
    )

    # Progress bar at bottom
    if climax.progress_bar_enabled:
        frac = max(0.0, min(1.0, rel_time_sec / eff_dur))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor_from_hex(climax.progress_bar_color_hex, "#FACC15"))
        painter.drawRect(QRectF(0.0, float(ch - 8), float(cw) * frac, 8.0))

    painter.end()
    return canvas_img


def render_angled_split_assets(
    width: int,
    height: int,
    split_ratio_top: float,
    divider: DividerConfig,
    out_dir: Path,
    prefix: str = "_cmp",
) -> tuple[Path, Optional[Path]]:
    """Generate grayscale alpha mask PNG and optional RGBA divider line PNG for angled FFmpeg split."""
    ensure_qt_gui_app()
    geom = compute_split_geometry(width, height, split_ratio_top, divider.angle_deg)

    mask_img = QImage(width, height, QImage.Format.Format_Grayscale8)
    mask_img.fill(Qt.GlobalColor.black)
    mp = QPainter(mask_img)
    mp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    mp.setPen(Qt.PenStyle.NoPen)
    mp.setBrush(Qt.GlobalColor.white)
    mp.drawPolygon(
        QPolygonF(
            [
                QPointF(0.0, 0.0),
                QPointF(float(width), 0.0),
                QPointF(float(width), geom.right_y),
                QPointF(0.0, geom.left_y),
            ]
        )
    )
    mp.end()

    mask_path = out_dir / f"{prefix}_angled_mask.png"
    mask_img.save(str(mask_path), "PNG")

    div_path: Optional[Path] = None
    if divider.enabled and divider.thickness_px > 0:
        div_img = QImage(width, height, QImage.Format.Format_RGBA8888)
        div_img.fill(Qt.GlobalColor.transparent)
        dp = QPainter(div_img)
        draw_divider_on_painter(dp, width, height, split_ratio_top, divider)
        dp.end()
        div_path = out_dir / f"{prefix}_angled_div.png"
        div_img.save(str(div_path), "PNG")

    return mask_path, div_path


@dataclass
class ComposerFFmpegPlan:
    """Complete FFmpeg filtergraph + extra PNG asset inputs + ASS path for ClipComposerConfig."""

    filter_lines: list[str]
    extra_video_inputs: list[tuple[Path, bool]] = field(default_factory=list)
    ass_path: Optional[Path] = None
    temp_fonts_dir: Optional[Path] = None
    temp_asset_paths: list[Path] = field(default_factory=list)
    sfx_events: list[tuple[float, Path, float]] = field(default_factory=list)
    video_output_label: str = "vout"
    output_width: int = 1080
    output_height: int = 1920
    summary: str = "composer"


def _escape_filter_val(val: str) -> str:
    return val.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


@dataclass
class _StaticMaskIndices:
    """Shared split labels or single PNG input indices for shape masks/borders across shots."""

    bf_mask_lbls: list[str] = field(default_factory=list)
    bf_border_lbls: list[str] = field(default_factory=list)
    pip_mask_lbls: list[str] = field(default_factory=list)
    pip_border_lbls: list[str] = field(default_factory=list)
    ang_mask_lbls: list[str] = field(default_factory=list)
    ang_div_lbls: list[str] = field(default_factory=list)


def _prepare_static_masks(
    layout: LayoutLayerConfig,
    aspect_ratio: str,
    cw: int,
    ch: int,
    n_shots: int,
    fps_safe: float,
    work_dir: Path,
    prefix: str,
    lines: list[str],
    extra_inputs: list[tuple[Path, bool]],
    temp_assets: list[Path],
    next_idx: int,
) -> tuple[_StaticMaskIndices, int]:
    """Generate shared PNG masks/borders once and split them across N shots when needed."""
    masks = _StaticMaskIndices()
    if aspect_ratio == "16:9":
        return masks, next_idx

    loop_flag = n_shots > 1

    def _register_png(path: Path, fmt: str, tag: str) -> list[str]:
        nonlocal next_idx
        in_idx = next_idx
        next_idx += 1
        extra_inputs.append((path, loop_flag))
        temp_assets.append(path)
        if n_shots == 1:
            lbl = f"{prefix}_{tag}_s0"
            lines.append(f"[{in_idx}:v]format={fmt}[{lbl}]")
            return [lbl]
        lbls = [f"{prefix}_{tag}_s{i}" for i in range(n_shots)]
        split_str = "".join(f"[{l}]" for l in lbls)
        lines.append(f"[{in_idx}:v]fps={fps_safe:.3f},format={fmt},split={n_shots}{split_str}")
        return lbls

    mode = layout.mode
    if mode == "blurred_frame" and (layout.frame_corner_radius_px > 0 or layout.frame_border_width_px > 0):
        fg_ratio_map = {"1:1": (cw, cw), "4:3": (cw, int(round(cw * 0.75))), "16:9": (cw, int(round(cw * 9.0 / 16.0)))}
        fg_w, fg_h = fg_ratio_map.get(layout.blurred_frame_aspect, (cw, int(round(cw * 0.75))))
        fg_h = min(ch, _make_even(fg_h, 2, ch))
        fg_mask, fg_border = render_frame_shape_assets(
            width=fg_w,
            height=fg_h,
            shape="rounded_rect",
            corner_radius_px=layout.frame_corner_radius_px,
            border_width_px=layout.frame_border_width_px,
            border_color_hex=layout.frame_border_color_hex,
            out_dir=work_dir,
            prefix=f"{prefix}_bf",
        )
        masks.bf_mask_lbls = _register_png(fg_mask, "gray", "bf_m")
        if fg_border is not None:
            masks.bf_border_lbls = _register_png(fg_border, "rgba", "bf_b")
    elif mode == "pip_facecam":
        _, _, pip_w, pip_h = compute_pip_rect(cw, ch, layout)
        pip_mask, pip_border = render_frame_shape_assets(
            width=pip_w,
            height=pip_h,
            shape=layout.pip_shape,
            corner_radius_px=layout.frame_corner_radius_px,
            border_width_px=layout.frame_border_width_px,
            border_color_hex=layout.frame_border_color_hex,
            out_dir=work_dir,
            prefix=f"{prefix}_pip",
        )
        masks.pip_mask_lbls = _register_png(pip_mask, "gray", "pip_m")
        if pip_border is not None:
            masks.pip_border_lbls = _register_png(pip_border, "rgba", "pip_b")
    elif mode not in ("single_focus", "blurred_frame", "pip_facecam"):
        geom = compute_split_geometry(cw, ch, layout.split_ratio_top, layout.divider.angle_deg)
        if geom.is_angled:
            ang_mask, ang_div = render_angled_split_assets(
                cw, ch, layout.split_ratio_top, layout.divider, work_dir, prefix=prefix
            )
            masks.ang_mask_lbls = _register_png(ang_mask, "gray", "ang_m")
            if ang_div is not None:
                masks.ang_div_lbls = _register_png(ang_div, "rgba", "ang_d")

    return masks, next_idx


def _emit_layout_for_stream(
    in_lbl: str,
    out_lbl: str,
    seg_prefix: str,
    shot_idx: int,
    seg_dur_sec: Optional[float],
    top_crop: CropBoxConfig,
    bot_crop: CropBoxConfig,
    layout: LayoutLayerConfig,
    aspect_ratio: str,
    src_w: int,
    src_h: int,
    cw: int,
    ch: int,
    masks: _StaticMaskIndices,
    lines: list[str],
) -> str:
    """Emit layout crop/composite filters from `in_lbl` into `out_lbl` (`yuv420p`, `setsar=1`)."""
    mode = layout.mode
    if aspect_ratio == "16:9":
        crop_w, crop_h, crop_x, crop_y = compute_pixel_crop_rect(top_crop, src_w, src_h, cw, ch)
        lines.append(
            f"[{in_lbl}]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={cw}:{ch}:flags=lanczos,format=yuv420p,setsar=1[{out_lbl}]"
        )
        return "16:9_custom"

    if mode == "single_focus":
        crop_w, crop_h, crop_x, crop_y = compute_pixel_crop_rect(top_crop, src_w, src_h, cw, ch)
        lines.append(
            f"[{in_lbl}]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={cw}:{ch}:flags=lanczos,format=yuv420p,setsar=1[{out_lbl}]"
        )
        return "single_focus"

    def _bound_mask(lbl: str, tag: str) -> str:
        if seg_dur_sec is None:
            return lbl
        trimmed = f"{seg_prefix}_{tag}_tr"
        lines.append(f"[{lbl}]trim=0:{seg_dur_sec:.3f},setpts=PTS-STARTPTS[{trimmed}]")
        return trimmed

    if mode == "blurred_frame":
        bg_w, bg_h, bg_x, bg_y = compute_pixel_crop_rect(bot_crop, src_w, src_h, cw, ch)
        fg_ratio_map = {"1:1": (cw, cw), "4:3": (cw, int(round(cw * 0.75))), "16:9": (cw, int(round(cw * 9.0 / 16.0)))}
        fg_w, fg_h = fg_ratio_map.get(layout.blurred_frame_aspect, (cw, int(round(cw * 0.75))))
        fg_h = min(ch, _make_even(fg_h, 2, ch))
        fc_w, fc_h, fc_x, fc_y = compute_pixel_crop_rect(top_crop, src_w, src_h, fg_w, fg_h)
        blur_r = max(2, min(60, int(layout.background_blur_strength)))
        darken = max(-0.5, min(0.3, float(layout.background_darken)))
        y_off = max(0, (ch - fg_h) // 2)

        lines.extend(
            [
                f"[{in_lbl}]split=2[{seg_prefix}_bg_in][{seg_prefix}_fg_in]",
                (
                    f"[{seg_prefix}_bg_in]crop={bg_w}:{bg_h}:{bg_x}:{bg_y},"
                    f"scale={cw}:{ch}:flags=bilinear,boxblur={blur_r}:3,eq=brightness={darken:.2f},setsar=1[{seg_prefix}_bg]"
                ),
            ]
        )
        if masks.bf_mask_lbls:
            m_lbl = _bound_mask(masks.bf_mask_lbls[shot_idx], "bfm")
            lines.extend(
                [
                    f"[{seg_prefix}_fg_in]crop={fc_w}:{fc_h}:{fc_x}:{fc_y},scale={fg_w}:{fg_h}:flags=lanczos,format=rgba,setsar=1[{seg_prefix}_fg]",
                    f"[{seg_prefix}_fg][{m_lbl}]alphamerge[{seg_prefix}_fg_a]",
                ]
            )
            if masks.bf_border_lbls:
                b_lbl = _bound_mask(masks.bf_border_lbls[shot_idx], "bfb")
                lines.append(f"[{seg_prefix}_bg][{seg_prefix}_fg_a]overlay=x=0:y={y_off}:shortest=0[{seg_prefix}_bf_cmp]")
                lines.append(f"[{seg_prefix}_bf_cmp][{b_lbl}]overlay=x=0:y={y_off}:shortest=0,format=yuv420p,setsar=1[{out_lbl}]")
            else:
                lines.append(f"[{seg_prefix}_bg][{seg_prefix}_fg_a]overlay=x=0:y={y_off}:shortest=0,format=yuv420p,setsar=1[{out_lbl}]")
        else:
            lines.extend(
                [
                    f"[{seg_prefix}_fg_in]crop={fc_w}:{fc_h}:{fc_x}:{fc_y},scale={fg_w}:{fg_h}:flags=lanczos,setsar=1[{seg_prefix}_fg]",
                    f"[{seg_prefix}_bg][{seg_prefix}_fg]overlay=x=(W-w)/2:y=(H-h)/2,format=yuv420p,setsar=1[{out_lbl}]",
                ]
            )
        return f"blurred_frame ({layout.blurred_frame_aspect})"

    if mode == "pip_facecam":
        bg_w, bg_h, bg_x, bg_y = compute_pixel_crop_rect(bot_crop, src_w, src_h, cw, ch)
        px, py, pip_w, pip_h = compute_pip_rect(cw, ch, layout)
        pc_w, pc_h, pc_x, pc_y = compute_pixel_crop_rect(top_crop, src_w, src_h, pip_w, pip_h)
        m_lbl = _bound_mask(masks.pip_mask_lbls[shot_idx], "pm")

        lines.extend(
            [
                f"[{in_lbl}]split=2[{seg_prefix}_bg_in][{seg_prefix}_pip_in]",
                f"[{seg_prefix}_bg_in]crop={bg_w}:{bg_h}:{bg_x}:{bg_y},scale={cw}:{ch}:flags=lanczos,setsar=1[{seg_prefix}_bg]",
                f"[{seg_prefix}_pip_in]crop={pc_w}:{pc_h}:{pc_x}:{pc_y},scale={pip_w}:{pip_h}:flags=lanczos,format=rgba,setsar=1[{seg_prefix}_pip_fg]",
                f"[{seg_prefix}_pip_fg][{m_lbl}]alphamerge[{seg_prefix}_pip_a]",
            ]
        )
        if masks.pip_border_lbls:
            b_lbl = _bound_mask(masks.pip_border_lbls[shot_idx], "pb")
            lines.append(f"[{seg_prefix}_bg][{seg_prefix}_pip_a]overlay=x={px}:y={py}:shortest=0[{seg_prefix}_pip_cmp]")
            lines.append(f"[{seg_prefix}_pip_cmp][{b_lbl}]overlay=x={px}:y={py}:shortest=0,format=yuv420p,setsar=1[{out_lbl}]")
        else:
            lines.append(f"[{seg_prefix}_bg][{seg_prefix}_pip_a]overlay=x={px}:y={py}:shortest=0,format=yuv420p,setsar=1[{out_lbl}]")
        return f"pip_facecam ({layout.pip_shape} {layout.pip_position})"

    # split_screen or multi_speaker_split
    geom = compute_split_geometry(cw, ch, layout.split_ratio_top, layout.divider.angle_deg)
    tc_w, tc_h, tc_x, tc_y = compute_pixel_crop_rect(top_crop, src_w, src_h, cw, geom.top_panel_h)
    bc_w, bc_h, bc_x, bc_y = compute_pixel_crop_rect(bot_crop, src_w, src_h, cw, geom.bot_panel_h)

    lines.append(f"[{in_lbl}]split=2[{seg_prefix}_t_in][{seg_prefix}_b_in]")
    lines.append(
        f"[{seg_prefix}_t_in]crop={tc_w}:{tc_h}:{tc_x}:{tc_y},"
        f"scale={cw}:{geom.top_panel_h}:flags=lanczos,setsar=1[{seg_prefix}_top]"
    )
    lines.append(
        f"[{seg_prefix}_b_in]crop={bc_w}:{bc_h}:{bc_x}:{bc_y},"
        f"scale={cw}:{geom.bot_panel_h}:flags=lanczos,setsar=1[{seg_prefix}_bot]"
    )

    if not geom.is_angled:
        stack_lbl = f"{seg_prefix}_stacked"
        lines.append(f"[{seg_prefix}_top][{seg_prefix}_bot]vstack=inputs=2,setsar=1[{stack_lbl}]")
        if layout.divider.enabled and layout.divider.thickness_px > 0:
            th = int(layout.divider.thickness_px)
            y_border = max(0, geom.split_y - (th + 4) // 2)
            y_main = max(0, geom.split_y - th // 2)
            b_col = layout.divider.border_color_hex[:7]
            m_col = layout.divider.color_hex[:7]
            lines.append(
                f"[{stack_lbl}]drawbox=x=0:y={y_border}:w={cw}:h={th + 4}:color={b_col}:t=fill,"
                f"drawbox=x=0:y={y_main}:w={cw}:h={th}:color={m_col}:t=fill,format=yuv420p,setsar=1[{out_lbl}]"
            )
        else:
            lines.append(f"[{stack_lbl}]format=yuv420p,setsar=1[{out_lbl}]")
        return f"{mode} ({int(round(layout.split_ratio_top*100))}/{int(round((1-layout.split_ratio_top)*100))})"

    m_lbl = _bound_mask(masks.ang_mask_lbls[shot_idx], "am")
    lines.extend(
        [
            f"[{seg_prefix}_top]pad={cw}:{ch}:0:0:black,format=rgba[{seg_prefix}_t_pad]",
            f"[{seg_prefix}_t_pad][{m_lbl}]alphamerge[{seg_prefix}_t_alpha]",
            f"[{seg_prefix}_bot]pad={cw}:{ch}:0:{geom.bot_panel_y}:black,format=rgba[{seg_prefix}_b_pad]",
        ]
    )
    if masks.ang_div_lbls:
        d_lbl = _bound_mask(masks.ang_div_lbls[shot_idx], "ad")
        lines.append(f"[{seg_prefix}_b_pad][{seg_prefix}_t_alpha]overlay=0:0:shortest=0[{seg_prefix}_ang_cmp]")
        lines.append(f"[{seg_prefix}_ang_cmp][{d_lbl}]overlay=0:0:shortest=0,format=yuv420p,setsar=1[{out_lbl}]")
    else:
        lines.append(f"[{seg_prefix}_b_pad][{seg_prefix}_t_alpha]overlay=0:0:shortest=0,format=yuv420p,setsar=1[{out_lbl}]")
    return f"{mode} angled ({layout.divider.angle_deg:+.1f}deg)"


def _normalize_shots_for_compile(
    layout: LayoutLayerConfig,
    main_dur: float,
    aspect_ratio: str,
) -> list[ShotCropSegment]:
    """Normalize valid auto-face `shot_crops` into contiguous segments covering `[0, main_dur]`."""
    if aspect_ratio == "16:9" or layout.framing_tracking_mode != "auto_face" or len(layout.shot_crops) <= 1:
        return []
    raw: list[ShotCropSegment] = []
    for seg in layout.shot_crops:
        t0 = max(0.0, round(float(seg.t_start), 3))
        t1 = min(main_dur, round(float(seg.t_end), 3))
        if t0 < main_dur - 0.08 and t1 - t0 >= 0.08:
            cp = seg.model_copy(deep=True)
            cp.t_start = t0
            cp.t_end = t1
            raw.append(cp)
    if len(raw) <= 1:
        return []
    raw[0].t_start = 0.0
    raw[-1].t_end = round(main_dur, 3)
    for i in range(1, len(raw)):
        mid = round(raw[i].t_start, 3)
        raw[i - 1].t_end = mid
        raw[i].t_start = mid
    return [s for s in raw if s.t_end - s.t_start >= 0.08]


def compile_composer_ffmpeg_plan(
    config: ClipComposerConfig,
    src_w: int,
    src_h: int,
    fps: float,
    duration_sec: float,
    work_dir: Path,
    segments: list[TranscriptSegment],
    opening_duration_sec: float = 0.0,
    prefix: str = "_cmp",
    base_extra_input_idx: int = 1,
    clip_start_sec: Optional[float] = None,
    clip_end_sec: Optional[float] = None,
) -> ComposerFFmpegPlan:
    """Translate ClipComposerConfig into a 1:1 WYSIWYG FFmpeg filtergraph & overlay asset set."""
    work_dir.mkdir(parents=True, exist_ok=True)
    cw = max(320, int(config.canvas_width))
    ch = max(320, int(config.canvas_height))
    intro_shift = max(0.0, float(opening_duration_sec))
    main_dur = max(0.5, float(duration_sec))
    total_dur = round(main_dur + intro_shift, 3)

    lines: list[str] = []
    extra_inputs: list[tuple[Path, bool]] = []
    temp_assets: list[Path] = []
    sfx_events: list[tuple[float, Path, float]] = []
    next_idx = int(base_extra_input_idx)

    layout = config.layout
    src_v = f"{prefix}_vsrc"
    lines.append(f"[0:v]setpts=PTS-STARTPTS[{src_v}]")

    fps_safe = max(10.0, min(120.0, float(fps or 30.0)))
    valid_shots = _normalize_shots_for_compile(layout, main_dur, config.aspect_ratio)
    n_shots = max(1, len(valid_shots))

    masks, next_idx = _prepare_static_masks(
        layout=layout,
        aspect_ratio=config.aspect_ratio,
        cw=cw,
        ch=ch,
        n_shots=n_shots,
        fps_safe=fps_safe,
        work_dir=work_dir,
        prefix=prefix,
        lines=lines,
        extra_inputs=extra_inputs,
        temp_assets=temp_assets,
        next_idx=next_idx,
    )

    if len(valid_shots) <= 1:
        active_top, active_bot = layout.resolve_crops_at_time(0.0)
        summary = _emit_layout_for_stream(
            in_lbl=src_v,
            out_lbl="framed",
            seg_prefix=prefix,
            shot_idx=0,
            seg_dur_sec=None,
            top_crop=active_top,
            bot_crop=active_bot,
            layout=layout,
            aspect_ratio=config.aspect_ratio,
            src_w=src_w,
            src_h=src_h,
            cw=cw,
            ch=ch,
            masks=masks,
            lines=lines,
        )
    else:
        resolved_trs = resolve_shot_transitions(valid_shots, layout.transition)
        split_lbls = "".join(f"[{prefix}_s{i}]" for i in range(n_shots))
        lines.append(f"[{src_v}]fps={fps_safe:.3f},split={n_shots}{split_lbls}")

        post_fx_windows: list[tuple[float, float, str]] = []
        base_summary = layout.mode

        for i, seg in enumerate(valid_shots):
            seg_dur = round(float(seg.t_end) - float(seg.t_start), 3)
            d_out = resolved_trs[i + 1].duration_sec if (i + 1 < n_shots and resolved_trs[i + 1] is not None) else 0.0
            in_tr = resolved_trs[i]
            tr_in_d = in_tr.duration_sec if in_tr is not None else 0.0
            _, post_fx_in = map_transition_to_ffmpeg_xfade(in_tr.tr_type) if in_tr is not None else ("fade", None)

            trim_lbl = f"{prefix}_st{i}"
            lines.append(f"[{prefix}_s{i}]trim=start={seg.t_start:.3f}:end={seg.t_end:.3f},setpts=PTS-STARTPTS[{trim_lbl}]")

            comp_lbl = f"{prefix}_sf{i}"
            base_summary = _emit_layout_for_stream(
                in_lbl=trim_lbl,
                out_lbl=comp_lbl,
                seg_prefix=f"{prefix}_sh{i}",
                shot_idx=i,
                seg_dur_sec=seg_dur,
                top_crop=seg.top_crop,
                bot_crop=seg.bottom_crop or layout.bottom_crop,
                layout=layout,
                aspect_ratio=config.aspect_ratio,
                src_w=src_w,
                src_h=src_h,
                cw=cw,
                ch=ch,
                masks=masks,
                lines=lines,
            )

            cur_seg_lbl = comp_lbl
            if post_fx_in == "zoom_out" and tr_in_d > 0.0:
                z_lbl = f"{prefix}_sz{i}"
                lines.append(
                    f"[{cur_seg_lbl}]scale=w='if(lt(t,{tr_in_d:.3f}),trunc({cw}*(1+0.18*(1-t/{tr_in_d:.3f}))/2)*2,{cw})':"
                    f"h='if(lt(t,{tr_in_d:.3f}),trunc({ch}*(1+0.18*(1-t/{tr_in_d:.3f}))/2)*2,{ch})':eval=frame,"
                    f"crop={cw}:{ch}:(iw-{cw})/2:(ih-{ch})/2,format=yuv420p,setsar=1[{z_lbl}]"
                )
                cur_seg_lbl = z_lbl

            out_seg = f"{prefix}_sv{i}"
            if d_out > 0.0:
                lines.append(
                    f"[{cur_seg_lbl}]tpad=stop_mode=clone:stop_duration={d_out:.3f},"
                    f"trim=0:{seg_dur + d_out:.3f},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[{out_seg}]"
                )
            else:
                lines.append(f"[{cur_seg_lbl}]setpts=PTS-STARTPTS,format=yuv420p,setsar=1[{out_seg}]")

        # Partition shots into xfade runs separated by hard-cut boundaries (where resolved_trs[j] is None)
        run_outputs: list[str] = []
        idx = 0
        while idx < n_shots:
            run_start = idx
            cur_run_lbl = f"{prefix}_sv{run_start}"
            acc_dur = round(float(valid_shots[run_start].t_end) - float(valid_shots[run_start].t_start), 3)
            j = run_start + 1
            while j < n_shots and resolved_trs[j] is not None:
                tr = resolved_trs[j]
                assert tr is not None
                xf_name, post_fx = map_transition_to_ffmpeg_xfade(tr.tr_type)
                nxt_lbl = f"{prefix}_xf_{j}"
                lines.append(
                    f"[{cur_run_lbl}][{prefix}_sv{j}]xfade=transition={xf_name}:"
                    f"duration={tr.duration_sec:.3f}:offset={acc_dur:.3f},setsar=1[{nxt_lbl}]"
                )
                cur_run_lbl = nxt_lbl
                acc_dur = round(acc_dur + (float(valid_shots[j].t_end) - float(valid_shots[j].t_start)), 3)
                if post_fx in ("whip", "blur_swap", "glitch"):
                    post_fx_windows.append((tr.cut_time_sec, round(tr.cut_time_sec + tr.duration_sec, 3), post_fx))
                if tr.sfx != "none":
                    wav_path = resolve_transition_sfx_path(tr.sfx)
                    if wav_path is not None:
                        sfx_events.append((round(intro_shift + tr.cut_time_sec, 3), wav_path, tr.sfx_volume))
                j += 1
            run_outputs.append(cur_run_lbl)
            idx = j

        if len(run_outputs) == 1:
            pre_fx_lbl = run_outputs[0]
        else:
            pre_fx_lbl = f"{prefix}_concat_tr"
            run_ins = "".join(f"[{r}]" for r in run_outputs)
            lines.append(f"{run_ins}concat=n={len(run_outputs)}:v=1:a=0,setsar=1[{pre_fx_lbl}]")

        fx_filters: list[str] = []
        for t0_fx, t1_fx, fx_kind in post_fx_windows:
            if fx_kind == "whip":
                fx_filters.append(f"avgblur=sizeX=20:sizeY=2:enable='between(t,{t0_fx:.3f},{t1_fx:.3f})'")
            elif fx_kind == "blur_swap":
                fx_filters.append(f"boxblur=10:2:enable='between(t,{t0_fx:.3f},{t1_fx:.3f})'")
            elif fx_kind == "glitch":
                fx_filters.append(f"rgbashift=rh=8:bh=-8:enable='between(t,{t0_fx:.3f},{t1_fx:.3f})'")

        if fx_filters:
            lines.append(f"[{pre_fx_lbl}]{','.join(fx_filters)},format=yuv420p,setsar=1[framed]")
        else:
            lines.append(f"[{pre_fx_lbl}]format=yuv420p,setsar=1[framed]")

        tr_cnt = sum(1 for t in resolved_trs if t is not None)
        if tr_cnt > 0:
            summary = f"{base_summary} ({n_shots} angles, {tr_cnt} transisi)"
        else:
            summary = f"{base_summary} ({n_shots} angles)"

    cur_lbl = "framed"

    # Optional frozen blurred intro for opening TTS
    if intro_shift > 0.0:
        fps_safe = max(10.0, min(120.0, float(fps or 30.0)))
        lines.extend(
            [
                f"[{cur_lbl}]fps={fps_safe:.3f},split=2[{prefix}_intro_src][{prefix}_main_src]",
                (
                    f"[{prefix}_intro_src]trim=start_frame=0:end_frame=1,"
                    f"tpad=stop_mode=clone:stop_duration={intro_shift:.3f},"
                    f"trim=0:{intro_shift:.3f},setpts=PTS-STARTPTS,"
                    f"boxblur=10:2,eq=brightness=-0.16,setsar=1[{prefix}_v_intro]"
                ),
                f"[{prefix}_main_src]setpts=PTS-STARTPTS[{prefix}_v_main]",
                f"[{prefix}_v_intro][{prefix}_v_main]concat=n=2:v=1:a=0,setsar=1[{prefix}_with_intro]",
            ]
        )
        cur_lbl = f"{prefix}_with_intro"

    # Climax effects (contrast/saturation + zoom punch)
    climax = config.overlays.climax_effects
    if climax.contrast_boost_enabled:
        next_lbl = f"{prefix}_eq"
        lines.append(
            f"[{cur_lbl}]eq=contrast={climax.contrast:.2f}:saturation={climax.saturation:.2f}:brightness={climax.brightness:.2f}[{next_lbl}]"
        )
        cur_lbl = next_lbl

    if climax.zoom_punch_enabled:
        zs = round(float(climax.zoom_punch_start_sec) + intro_shift, 3)
        ze = min(total_dur, round(zs + float(climax.zoom_punch_duration_sec), 3))
        if ze > zs:
            zw = _make_even(cw * float(climax.zoom_punch_factor), 2)
            zh = _make_even(ch * float(climax.zoom_punch_factor), 2)
            next_lbl = f"{prefix}_zoom"
            lines.append(
                f"[{cur_lbl}]scale=w='if(between(t,{zs:.3f},{ze:.3f}),{zw},{cw})':"
                f"h='if(between(t,{zs:.3f},{ze:.3f}),{zh},{ch})':eval=frame,"
                f"crop={cw}:{ch}:(iw-{cw})/2:(ih-{ch})/2[{next_lbl}]"
            )
            cur_lbl = next_lbl

    if climax.vignette_enabled:
        next_lbl = f"{prefix}_vig"
        lines.append(f"[{cur_lbl}]vignette=angle=PI/5[{next_lbl}]")
        cur_lbl = next_lbl

    # Persistent Top Topic Header Pill
    topic_card = render_topic_header_card_image(climax, canvas_w=cw)
    if topic_card is not None:
        t_img, tx, ty = topic_card
        t_png = work_dir / f"{prefix}_topic.png"
        t_img.save(str(t_png), "PNG")
        temp_assets.append(t_png)
        t_idx = next_idx
        next_idx += 1
        extra_inputs.append((t_png, False))
        next_lbl = f"{prefix}_ov_topic"
        lines.append(f"[{cur_lbl}][{t_idx}:v]overlay=x={tx}:y={ty}:shortest=0[{next_lbl}]")
        cur_lbl = next_lbl

    # Hook Banner PNG overlay with transition
    hook = config.overlays.hook_banner
    hook_card = render_hook_banner_card_image(hook, canvas_w=cw)
    if hook_card is not None:
        h_img, hx, hy = hook_card
        hy = min(ch - h_img.height() - 12, hy)
        hook_png = work_dir / f"{prefix}_hook.png"
        h_img.save(str(hook_png), "PNG")
        temp_assets.append(hook_png)
        h_idx = next_idx
        next_idx += 1
        h_dur = min(total_dur, max(0.3, float(hook.duration_sec)))
        use_loop = hook.transition_in in ("fade", "slide", "zoom") or hook.transition_out == "fade"
        extra_inputs.append((hook_png, use_loop))

        h_stream = f"[{h_idx}:v]"
        if use_loop:
            fade_d = min(0.22, h_dur * 0.35)
            fade_out_st = max(0.0, round(h_dur - fade_d, 3))
            h_faded = f"{prefix}_hook_a"
            lines.append(
                f"{h_stream}format=rgba,"
                f"fade=t=in:st=0:d={fade_d:.3f}:alpha=1,"
                f"fade=t=out:st={fade_out_st:.3f}:d={fade_d:.3f}:alpha=1[{h_faded}]"
            )
            h_stream = f"[{h_faded}]"

        x_expr = f"{hx}"
        y_expr = f"{hy}"
        if hook.transition_in == "slide":
            y_expr = f"if(lt(t,0.22),{hy}-(1-t/0.22)*180,{hy})"
        elif hook.transition_in == "whip":
            x_expr = f"if(lt(t,0.18),{hx}-(1-t/0.18)*{cw},{hx})"

        next_lbl = f"{prefix}_ov_hook"
        shortest_opt = ":shortest=1" if use_loop else ""
        lines.append(
            f"[{cur_lbl}]{h_stream}overlay=x='{x_expr}':y='{y_expr}'{shortest_opt}:"
            f"enable='between(t,0,{h_dur:.3f})'[{next_lbl}]"
        )
        cur_lbl = next_lbl

    # Viewer Comment Bubbles PNG overlays
    for b_idx, bubble in enumerate(config.overlays.comment_bubbles):
        if not bubble.enabled:
            continue
        bs = round(float(bubble.start_sec) + intro_shift, 3)
        be = min(total_dur, round(float(bubble.end_sec) + intro_shift, 3))
        if be <= bs:
            continue
        b_img, bx, by = render_comment_bubble_card_image(bubble, canvas_w=cw, canvas_h=ch)
        b_png = work_dir / f"{prefix}_bubble_{b_idx}.png"
        b_img.save(str(b_png), "PNG")
        temp_assets.append(b_png)
        c_idx = next_idx
        next_idx += 1
        extra_inputs.append((b_png, False))
        next_lbl = f"{prefix}_ov_cb_{b_idx}"
        lines.append(
            f"[{cur_lbl}][{c_idx}:v]overlay=x={bx}:y={by}:enable='between(t,{bs:.3f},{be:.3f})'[{next_lbl}]"
        )
        cur_lbl = next_lbl

    # Watermark PNG overlay
    wm_card = render_watermark_card_image(config.overlays.watermark, canvas_w=cw, canvas_h=ch)
    if wm_card is not None:
        w_img, wx, wy = wm_card
        w_png = work_dir / f"{prefix}_watermark.png"
        w_img.save(str(w_png), "PNG")
        temp_assets.append(w_png)
        w_idx = next_idx
        next_idx += 1
        extra_inputs.append((w_png, False))
        next_lbl = f"{prefix}_ov_wm"
        lines.append(f"[{cur_lbl}][{w_idx}:v]overlay=x={wx}:y={wy}[{next_lbl}]")
        cur_lbl = next_lbl

    # Bottom progress bar
    if climax.progress_bar_enabled:
        pbar_col = climax.progress_bar_color_hex[:7]
        pbar_lbl = f"{prefix}_pbar"
        next_lbl = f"{prefix}_with_pbar"
        lines.append(f"color=c={pbar_col}:s={cw}x8:d={total_dur:.3f}[{pbar_lbl}]")
        lines.append(
            f"[{cur_lbl}][{pbar_lbl}]overlay=x='-w+w*min(1,t/{total_dur:.3f})':y=H-8:shortest=1[{next_lbl}]"
        )
        cur_lbl = next_lbl

    # Custom font staging + ASS burn-in
    temp_fonts_dir: Optional[Path] = None
    ass_path = generate_composer_ass_file(
        output_ass_path=work_dir / f"{prefix}_subs.ass",
        config=config,
        segments=segments,
        clip_duration_sec=total_dur,
        opening_duration_sec=intro_shift,
        clip_start_sec=clip_start_sec,
        clip_end_sec=clip_end_sec,
    )
    if ass_path is not None:
        temp_assets.append(ass_path)
        fontsdir_opt = ""
        if config.subtitles.custom_font_path:
            fpath = Path(config.subtitles.custom_font_path)
            if fpath.is_file():
                temp_fonts_dir = work_dir / f"{prefix}_fonts"
                temp_fonts_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fpath, temp_fonts_dir / fpath.name)
                fontsdir_opt = f":fontsdir={_escape_filter_val(temp_fonts_dir.name)}"
        safe_ass = _escape_filter_val(ass_path.name)
        next_lbl = f"{prefix}_sub"
        lines.append(f"[{cur_lbl}]ass=filename={safe_ass}{fontsdir_opt}[{next_lbl}]")
        cur_lbl = next_lbl

    lines.append(f"[{cur_lbl}]format=yuv420p[vout]")

    return ComposerFFmpegPlan(
        filter_lines=lines,
        extra_video_inputs=extra_inputs,
        ass_path=ass_path,
        temp_fonts_dir=temp_fonts_dir,
        temp_asset_paths=temp_assets,
        sfx_events=sfx_events,
        video_output_label="vout",
        output_width=cw,
        output_height=ch,
        summary=summary,
    )
