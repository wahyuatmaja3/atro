"""Unified WYSIWYG shot transition planner, FFmpeg xfade mapper, and QImage blender."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from atro.core.composer_schema import (
    ShotCropSegment,
    TransitionConfig,
    TransitionEasing,
    TransitionSfx,
    TransitionType,
)

MAX_XFADE_NODES = 24

TRANSITION_CHOICES: tuple[tuple[TransitionType, str], ...] = (
    ("none", "Potongan Langsung (None)"),
    ("fade", "Fade Halus"),
    ("crossfade", "Crossfade (Dissolve)"),
    ("dip_black", "Dip ke Hitam (Dip Black)"),
    ("slide_left", "Geser Kiri (Slide Left)"),
    ("slide_right", "Geser Kanan (Slide Right)"),
    ("slide_up", "Geser Atas (Slide Up)"),
    ("slide_down", "Geser Bawah (Slide Down)"),
    ("zoom_in", "Zoom Masuk (Zoom In)"),
    ("zoom_out", "Zoom Keluar (Zoom Out)"),
    ("whip", "Whip Pan Cepat"),
    ("blur_swap", "Blur Swap"),
    ("glitch", "Glitch / Pixelize"),
    ("wipe", "Wipe Kiri"),
)


@dataclass(slots=True)
class ResolvedShotTransition:
    """Resolved concrete transition entering shot `cut_index` at `cut_time_sec`."""

    cut_index: int
    cut_time_sec: float
    tr_type: TransitionType
    duration_sec: float
    easing: TransitionEasing
    sfx: TransitionSfx
    sfx_volume: float


def apply_easing(u: float, easing: TransitionEasing = "ease_in_out") -> float:
    """Apply smooth easing curve to normalized transition progress `u in [0..1]`."""
    c = max(0.0, min(1.0, float(u)))
    if easing == "linear":
        return c
    if easing == "ease_out":
        inv = 1.0 - c
        return 1.0 - (inv * inv)
    # ease_in_out (smoothstep)
    return c * c * (3.0 - 2.0 * c)


def _pick_auto_transition(
    prev_seg: ShotCropSegment,
    cur_seg: ShotCropSegment,
    base_dur: float,
) -> tuple[TransitionType, float]:
    """Choose context-aware transition for podcast cuts & topic shifts."""
    prev_dur = max(0.0, float(prev_seg.t_end) - float(prev_seg.t_start))
    cur_dur = max(0.0, float(cur_seg.t_end) - float(cur_seg.t_start))
    dx = float(cur_seg.top_crop.x) - float(prev_seg.top_crop.x)

    if cur_seg.reason == "reaction":
        return "zoom_in", min(base_dur, 0.22)
    if cur_seg.reason == "group" or prev_seg.reason == "group":
        return "crossfade", min(0.35, max(0.20, base_dur))
    if prev_dur < 2.2 or cur_dur < 2.2:
        if abs(dx) >= 0.08:
            return ("slide_left" if dx > 0 else "slide_right"), min(base_dur, 0.20)
        return "whip", min(base_dur, 0.18)
    if abs(dx) >= 0.08:
        return ("slide_left" if dx > 0 else "slide_right"), min(base_dur, 0.25)
    return "fade", min(base_dur, 0.22)


def resolve_shot_transitions(
    shots: list[ShotCropSegment],
    global_tr: TransitionConfig,
) -> list[Optional[ResolvedShotTransition]]:
    """Resolve concrete transition params per shot boundary (`[None, tr1, tr2, ...]`)."""
    n = len(shots)
    out: list[Optional[ResolvedShotTransition]] = [None] * n
    if n < 2:
        return out

    active_count = 0
    for i in range(1, n):
        if active_count >= MAX_XFADE_NODES:
            break
        prev_seg = shots[i - 1]
        cur_seg = shots[i]
        prev_dur = max(0.0, float(prev_seg.t_end) - float(prev_seg.t_start))
        cur_dur = max(0.0, float(cur_seg.t_end) - float(cur_seg.t_start))
        if prev_dur < 0.50 or cur_dur < 0.50:
            continue

        cfg = cur_seg.transition_in if cur_seg.transition_in is not None else global_tr
        if cfg.mode == "fixed" and cfg.type == "none":
            continue

        base_dur = max(0.10, min(0.60, float(cfg.duration_sec)))
        if cfg.mode == "auto":
            chosen_type, base_dur = _pick_auto_transition(prev_seg, cur_seg, base_dur)
        else:
            chosen_type = cfg.type

        if chosen_type == "none":
            continue

        eff_dur = round(min(base_dur, prev_dur * 0.45, cur_dur * 0.45), 3)
        if eff_dur < 0.08:
            continue

        active_count += 1
        out[i] = ResolvedShotTransition(
            cut_index=i,
            cut_time_sec=round(float(cur_seg.t_start), 3),
            tr_type=chosen_type,
            duration_sec=eff_dur,
            easing=cfg.easing,
            sfx=cfg.sfx,
            sfx_volume=max(0.0, min(1.0, float(cfg.sfx_volume))),
        )
    return out


def active_transition_window_at(
    shots: list[ShotCropSegment],
    global_tr: TransitionConfig,
    rel_t: float,
) -> Optional[tuple[int, int, ResolvedShotTransition, float]]:
    """If `rel_t` falls inside a shot boundary transition `[cut_t, cut_t + d]`, return details."""
    resolved = resolve_shot_transitions(shots, global_tr)
    t = max(0.0, float(rel_t))
    for i in range(1, len(resolved)):
        tr = resolved[i]
        if tr is None:
            continue
        t0 = tr.cut_time_sec
        t1 = t0 + tr.duration_sec
        if t0 <= t < t1:
            prog = (t - t0) / max(1e-4, tr.duration_sec)
            return i - 1, i, tr, max(0.0, min(1.0, prog))
    return None


def map_transition_to_ffmpeg_xfade(tr_type: TransitionType) -> tuple[str, Optional[str]]:
    """Map semantic ATRO `TransitionType` to `(ffmpeg_xfade_name, extra_post_fx_tag)`."""
    mapping: dict[TransitionType, tuple[str, Optional[str]]] = {
        "none": ("fade", None),
        "fade": ("fade", None),
        "crossfade": ("dissolve", None),
        "dip_black": ("fadeblack", None),
        "slide_left": ("slideleft", None),
        "slide_right": ("slideright", None),
        "slide_up": ("slideup", None),
        "slide_down": ("slidedown", None),
        "zoom_in": ("zoomin", None),
        "zoom_out": ("fade", "zoom_out"),
        "whip": ("slideleft", "whip"),
        "blur_swap": ("fade", "blur_swap"),
        "glitch": ("pixelize", "glitch"),
        "wipe": ("wipeleft", None),
    }
    return mapping.get(tr_type, ("fade", None))


def _blur_qimage(img: QImage, radius_px: float) -> QImage:
    """Fast box/Gaussian-like downscale-upscale blur on QImage for WYSIWYG whip/blur_swap."""
    r = max(0.0, float(radius_px))
    if r < 1.0 or img.isNull():
        return img
    w, h = img.width(), img.height()
    factor = max(2.0, min(14.0, r * 0.45))
    sw = max(16, int(round(w / factor)))
    sh = max(16, int(round(h / factor)))
    small = img.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def blend_transition_qimages(
    img_a: QImage,
    img_b: QImage,
    progress: float,
    tr_type: TransitionType,
    easing: TransitionEasing = "ease_in_out",
) -> QImage:
    """Composite outgoing frame `img_a` into incoming frame `img_b` matching FFmpeg transition."""
    if tr_type == "none":
        return img_b

    u = apply_easing(progress, easing=easing)
    if u <= 0.001:
        return img_a
    if u >= 0.999:
        return img_b

    w = max(2, img_b.width())
    h = max(2, img_b.height())
    out = QImage(w, h, QImage.Format.Format_RGBA8888)
    out.fill(QColor(0, 0, 0, 255))

    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    if tr_type in ("fade", "crossfade"):
        p.drawImage(0, 0, img_a)
        p.setOpacity(u)
        p.drawImage(0, 0, img_b)
    elif tr_type == "dip_black":
        if u < 0.5:
            p.drawImage(0, 0, img_a)
            p.fillRect(0, 0, w, h, QColor(0, 0, 0, int(round((u * 2.0) * 255.0))))
        else:
            p.setOpacity((u - 0.5) * 2.0)
            p.drawImage(0, 0, img_b)
    elif tr_type == "slide_left":
        off = u * w
        p.drawImage(QPointF(-off, 0.0), img_a)
        p.drawImage(QPointF(w - off, 0.0), img_b)
    elif tr_type == "slide_right":
        off = u * w
        p.drawImage(QPointF(off, 0.0), img_a)
        p.drawImage(QPointF(off - w, 0.0), img_b)
    elif tr_type == "slide_up":
        off = u * h
        p.drawImage(QPointF(0.0, -off), img_a)
        p.drawImage(QPointF(0.0, h - off), img_b)
    elif tr_type == "slide_down":
        off = u * h
        p.drawImage(QPointF(0.0, off), img_a)
        p.drawImage(QPointF(0.0, off - h), img_b)
    elif tr_type == "whip":
        blur_amt = math.sin(u * math.pi) * 20.0
        ba = _blur_qimage(img_a, blur_amt)
        bb = _blur_qimage(img_b, blur_amt)
        off = u * w
        p.drawImage(QPointF(-off, 0.0), ba)
        p.drawImage(QPointF(w - off, 0.0), bb)
    elif tr_type == "blur_swap":
        blur_amt = math.sin(u * math.pi) * 24.0
        ba = _blur_qimage(img_a, blur_amt)
        bb = _blur_qimage(img_b, blur_amt)
        p.drawImage(0, 0, ba)
        p.setOpacity(u)
        p.drawImage(0, 0, bb)
    elif tr_type == "zoom_in":
        sa = 1.0 + 0.18 * u
        sb = 0.86 + 0.14 * u
        p.save()
        p.translate(w * 0.5, h * 0.5)
        p.scale(sa, sa)
        p.setOpacity(1.0 - u)
        p.drawImage(QPointF(-w * 0.5, -h * 0.5), img_a)
        p.restore()
        p.save()
        p.translate(w * 0.5, h * 0.5)
        p.scale(sb, sb)
        p.setOpacity(u)
        p.drawImage(QPointF(-w * 0.5, -h * 0.5), img_b)
        p.restore()
    elif tr_type == "zoom_out":
        sa = 1.0 - 0.12 * u
        sb = 1.18 - 0.18 * u
        p.save()
        p.translate(w * 0.5, h * 0.5)
        p.scale(sa, sa)
        p.setOpacity(1.0 - u)
        p.drawImage(QPointF(-w * 0.5, -h * 0.5), img_a)
        p.restore()
        p.save()
        p.translate(w * 0.5, h * 0.5)
        p.scale(sb, sb)
        p.setOpacity(u)
        p.drawImage(QPointF(-w * 0.5, -h * 0.5), img_b)
        p.restore()
    elif tr_type == "wipe":
        p.drawImage(0, 0, img_a)
        split_x = u * w
        p.setClipRect(QRectF(0.0, 0.0, split_x, float(h)))
        p.drawImage(0, 0, img_b)
    elif tr_type == "glitch":
        pix_blur = math.sin(u * math.pi) * 14.0
        base = _blur_qimage(img_a if u < 0.5 else img_b, pix_blur * 0.6)
        p.drawImage(0, 0, base)
        # Subtle glitch band shift
        band_h = max(12.0, h * 0.14)
        shift_x = (1.0 - abs(u - 0.5) * 2.0) * 24.0
        p.setClipRect(QRectF(0.0, h * 0.35, float(w), band_h))
        p.drawImage(QPointF(shift_x, 0.0), img_b)
        p.setClipRect(QRectF(0.0, h * 0.62, float(w), band_h))
        p.drawImage(QPointF(-shift_x, 0.0), img_b)
    else:
        p.drawImage(0, 0, img_a)
        p.setOpacity(u)
        p.drawImage(0, 0, img_b)

    p.end()
    return out
