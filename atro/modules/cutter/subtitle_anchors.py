"""Shared canvas geometry and speaker-following (`above_speaker`) caption anchor math."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from atro.core.composer_schema import (
    ClipComposerConfig,
    CropBoxConfig,
    FaceAnchorBox,
    LayoutLayerConfig,
    ShotCropSegment,
    resolve_bottom_safe_y,
)
from atro.modules.cutter.framing.face_tracking import _make_even
from atro.modules.cutter.sfx import get_sfx_context


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


def zoom_punch_windows(climax: Any, total_dur: float, shift: float = 0.0) -> list[tuple[float, float]]:
    """Return `(start, end)` zoom-punch windows; repeats every `zoom_punch_repeat_sec` when > 0."""
    if not climax.zoom_punch_enabled:
        return []
    dur = float(climax.zoom_punch_duration_sec)
    start = float(climax.zoom_punch_start_sec)
    repeat = float(getattr(climax, "zoom_punch_repeat_sec", 0.0) or 0.0)
    step = max(repeat, dur + 0.3) if repeat > 0.0 else 0.0
    wins: list[tuple[float, float]] = []
    s = start
    while s < total_dur and len(wins) < 60:
        e = min(total_dur, s + dur)
        if e > s:
            wins.append((round(s + shift, 3), round(e + shift, 3)))
        if step <= 0.0:
            break
        s += step
    return wins


@dataclass(slots=True)
class ZoomWindowSpec:
    """Resolved interactive zoom-in/zoom-out window with curve and normalized anchor."""

    t_start: float
    t_end: float
    factor: float
    anchor_x: float
    anchor_y: float
    curve: str = "step"


def _effective_shot_mode_at(layout: LayoutLayerConfig, rel_t: float) -> str:
    shot = _shot_at_time(layout, rel_t)
    if shot is not None and shot.shot_mode:
        if layout.mode == "single_focus" and shot.shot_mode == "blurred_frame":
            return "blurred_frame"
        if getattr(layout, "interactive_reaction_cuts", False) and shot.shot_mode in (
            "single_focus",
            "blurred_frame",
            "pip_facecam",
            "split_screen",
        ):
            return shot.shot_mode
    return layout.mode


def _resolve_zoom_anchor(
    config: ClipComposerConfig,
    role: str,
    rel_t: float,
    cw: int,
    ch: int,
) -> tuple[float, float]:
    clean = (role or "center").strip().lower()
    if clean in ("center", "none", ""):
        return 0.5, 0.5

    layout = config.layout
    eff_mode = _effective_shot_mode_at(layout, rel_t)

    if clean == "gameplay":
        if eff_mode == "split_screen":
            top_r = float(layout.split_ratio_top)
            return 0.5, round(max(0.52, min(0.84, top_r + (1.0 - top_r) * 0.50)), 4)
        return 0.5, 0.52

    # clean == "facecam"
    if eff_mode == "split_screen":
        top_r = float(layout.split_ratio_top)
        return 0.5, round(max(0.14, min(0.45, top_r * 0.50)), 4)
    if eff_mode == "pip_facecam":
        px, py, pip_w, pip_h = compute_pip_rect(cw, ch, layout)
        return round((px + pip_w * 0.5) / max(1, cw), 4), round((py + pip_h * 0.5) / max(1, ch), 4)
    if eff_mode == "single_focus":
        return 0.5, 0.42
    # blurred_frame: locate face in center frame if known
    shot = _shot_at_time(layout, rel_t)
    if shot is not None and shot.speaker_faces:
        face = next(iter(shot.speaker_faces.values()))
        crop, dest_x, dest_y, tw, th = _panel_for_face(face, config, shot, cw, ch)
        # Approximate relative position inside blurred frame panel
        fcx = (face.x + face.w * 0.5 - crop.x) / max(0.05, crop.w)
        fcy = (face.y + face.h * 0.4 - crop.y) / max(0.05, crop.h)
        ax = max(0.18, min(0.82, (dest_x + fcx * tw) / max(1, cw)))
        ay = max(0.18, min(0.82, (dest_y + fcy * th) / max(1, ch)))
        return round(ax, 4), round(ay, 4)
    return 0.5, 0.40


def _window_timings_for_curve(curve: str, dur: float) -> tuple[float, float]:
    d = max(0.2, float(dur))
    if curve == "snap_out":
        t_in = min(0.08, d * 0.14)
        t_out = max(0.12, d * 0.52)
        return round(t_in, 3), round(t_out, 3)
    t_in = max(0.10, min(0.42, d * 0.28))
    t_out = max(0.12, min(0.50, d * 0.34))
    return round(t_in, 3), round(t_out, 3)


def resolve_interactive_zoom_windows(
    config: ClipComposerConfig,
    total_dur: float,
    shift: float = 0.0,
) -> list[ZoomWindowSpec]:
    """Resolve non-overlapping interactive zoom-in/zoom-out windows synced to SFX cues or rhythmic beats."""
    climax = config.overlays.climax_effects
    if not climax.zoom_punch_enabled:
        return []

    cw = max(320, int(config.canvas_width))
    ch = max(320, int(config.canvas_height))
    main_dur = max(0.5, float(total_dur) - max(0.0, float(shift)))
    dur = max(0.25, float(climax.zoom_punch_duration_sec))
    factor = max(1.02, float(climax.zoom_punch_factor))
    curve = str(getattr(climax, "zoom_curve", "step") or "step")
    sync_sfx = bool(getattr(climax, "zoom_sync_sfx", False))
    target_mode = str(getattr(climax, "zoom_target", "center") or "center")
    repeat = float(getattr(climax, "zoom_punch_repeat_sec", 0.0) or 0.0)

    candidates: list[tuple[float, float, str]] = []
    lead = 0.14 if curve == "smooth_in_out" else (0.04 if curve == "snap_out" else 0.0)

    if sync_sfx and config.audio.contextual_sfx_enabled and config.audio.sfx_cues:
        for cue in sorted(config.audio.sfx_cues, key=lambda c: c.time_sec):
            ct = float(cue.time_sec)
            if ct < 0.0 or ct >= main_dur - 0.15:
                continue
            ws = max(0.0, round(ct - lead, 3))
            we = min(main_dur, round(ws + dur, 3))
            if we - ws < 0.25:
                continue
            spec = get_sfx_context(cue.sfx_name)
            if target_mode == "auto_context":
                role_hint = spec.zoom_role if (spec is not None and spec.zoom_role != "none") else "gameplay"
            else:
                role_hint = target_mode
            candidates.append((ws, we, role_hint))

    if not candidates:
        base_wins = zoom_punch_windows(climax, main_dur, shift=0.0)
        for idx, (ws, we) in enumerate(base_wins):
            if target_mode == "auto_context":
                shot = _shot_at_time(config.layout, 0.5 * (ws + we))
                if shot is not None and shot.reason == "reaction":
                    role_hint = "facecam"
                else:
                    role_hint = "gameplay" if idx % 2 == 0 else "facecam"
            else:
                role_hint = target_mode
            candidates.append((ws, we, role_hint))
    elif repeat > 0.0:
        # Fill long gaps between SFX cues so pacing stays interactive throughout the Short
        step = max(repeat, dur + 0.4)
        extra_t = max(float(climax.zoom_punch_start_sec), 0.8)
        alt_idx = 0
        while extra_t + 0.35 < main_dur and len(candidates) < 40:
            ws = round(extra_t, 3)
            we = min(main_dur, round(ws + dur, 3))
            overlaps = any(not (we <= cs - 0.35 or ws >= ce + 0.35) for cs, ce, _ in candidates)
            if not overlaps and (we - ws) >= 0.35:
                if target_mode == "auto_context":
                    role_hint = "gameplay" if alt_idx % 2 == 0 else "facecam"
                    alt_idx += 1
                else:
                    role_hint = target_mode
                candidates.append((ws, we, role_hint))
            extra_t += step

    candidates.sort(key=lambda item: item[0])
    out: list[ZoomWindowSpec] = []
    for ws, we, role_hint in candidates:
        if out and ws < out[-1].t_end - shift + 0.06:
            ws = round(out[-1].t_end - shift + 0.06, 3)
        if we - ws < 0.25 or ws >= main_dur:
            continue
        mid_t = 0.5 * (ws + we)
        ax, ay = _resolve_zoom_anchor(config, role_hint, mid_t, cw, ch)
        out.append(
            ZoomWindowSpec(
                t_start=round(ws + shift, 3),
                t_end=round(we + shift, 3),
                factor=factor,
                anchor_x=ax,
                anchor_y=ay,
                curve=curve,
            )
        )
        if len(out) >= 60:
            break
    return out


def evaluate_zoom_at_time(
    windows: Sequence[ZoomWindowSpec],
    rel_t: float,
) -> tuple[float, float, float]:
    """Return `(zoom_factor, anchor_x, anchor_y)` at clip-relative `rel_t`."""
    t = float(rel_t)
    for w in windows:
        if w.t_start <= t <= w.t_end:
            if w.curve == "step":
                p = 1.0
            else:
                t_in, t_out = _window_timings_for_curve(w.curve, w.t_end - w.t_start)
                r = min(1.0, max(0.0, min((t - w.t_start) / t_in, (w.t_end - t) / t_out)))
                p = 0.5 * (1.0 - math.cos(r * math.pi))
            return 1.0 + (max(1.01, float(w.factor)) - 1.0) * p, w.anchor_x, w.anchor_y
    return 1.0, 0.5, 0.5


def build_ffmpeg_zoom_filter_exprs(
    windows: Sequence[ZoomWindowSpec],
    cw: int,
    ch: int,
) -> tuple[str, str, str, str]:
    """Return `(w_expr, h_expr, x_expr, y_expr)` for FFmpeg `scale=...:eval=frame,crop=...`."""
    if not windows:
        return str(cw), str(ch), f"(iw-{cw})/2", f"(ih-{ch})/2"

    all_step_center = all(
        w.curve == "step" and abs(w.anchor_x - 0.5) < 0.005 and abs(w.anchor_y - 0.5) < 0.005
        for w in windows
    )
    if all_step_center:
        zw = _make_even(cw * float(windows[0].factor), 2)
        zh = _make_even(ch * float(windows[0].factor), 2)
        cond = "+".join(f"between(t,{w.t_start:.3f},{w.t_end:.3f})" for w in windows)
        return (
            f"if(gt({cond},0),{zw},{cw})",
            f"if(gt({cond},0),{zh},{ch})",
            f"(iw-{cw})/2",
            f"(ih-{ch})/2",
        )

    z_terms: list[str] = []
    ax_terms: list[str] = []
    ay_terms: list[str] = []
    for w in windows:
        dz = max(0.01, float(w.factor) - 1.0)
        if w.curve == "step":
            z_terms.append(f"between(t,{w.t_start:.3f},{w.t_end:.3f})*{dz:.4f}")
        else:
            t_in, t_out = _window_timings_for_curve(w.curve, w.t_end - w.t_start)
            z_terms.append(
                f"between(t,{w.t_start:.3f},{w.t_end:.3f})*{dz:.4f}"
                f"*0.5*(1-cos(clip(min((t-{w.t_start:.3f})/{t_in:.3f},({w.t_end:.3f}-t)/{t_out:.3f}),0,1)*PI))"
            )
        if abs(w.anchor_x - 0.5) > 0.005:
            ax_terms.append(f"between(t,{w.t_start:.3f},{w.t_end:.3f})*({w.anchor_x - 0.5:+.4f})")
        if abs(w.anchor_y - 0.5) > 0.005:
            ay_terms.append(f"between(t,{w.t_start:.3f},{w.t_end:.3f})*({w.anchor_y - 0.5:+.4f})")

    z_expr = "1+" + "+".join(z_terms)
    w_expr = f"max({cw},trunc(({cw}*({z_expr})+0.5)/2)*2)"
    h_expr = f"max({ch},trunc(({ch}*({z_expr})+0.5)/2)*2)"
    ax_expr = ("0.5+" + "+".join(ax_terms)) if ax_terms else "0.5"
    ay_expr = ("0.5+" + "+".join(ay_terms)) if ay_terms else "0.5"
    x_expr = f"clip(iw*({ax_expr})-{cw}/2,0,iw-{cw})"
    y_expr = f"clip(ih*({ay_expr})-{ch}/2,0,ih-{ch})"
    return w_expr, h_expr, x_expr, y_expr


def _speaker_aliases(speaker_id: Optional[str]) -> list[str]:
    if not speaker_id:
        return []
    clean = speaker_id.strip()
    out = [clean]
    upper = clean.upper()
    if upper.startswith("SPEAKER_"):
        suffix = upper.split("_", 1)[1]
        out.append(f"SPK_{suffix}")
    elif upper.startswith("SPK_"):
        suffix = upper.split("_", 1)[1]
        out.append(f"SPEAKER_{suffix}")
    return out


def _shot_at_time(layout: LayoutLayerConfig, rel_t: float) -> Optional[ShotCropSegment]:
    if not layout.shot_crops:
        return None
    t = max(0.0, float(rel_t))
    if t < layout.shot_crops[0].t_start:
        return layout.shot_crops[0]
    for seg in layout.shot_crops:
        if seg.t_start <= t < seg.t_end:
            return seg
    return layout.shot_crops[-1]


def _resolve_chunk_speaker(
    chunk: Any,
    layout: LayoutLayerConfig,
    shot: Optional[ShotCropSegment],
) -> tuple[str, float, bool]:
    """Return `(speaker_id, confidence, is_confident)` for one caption chunk."""
    raw_tag = (getattr(chunk, "speaker", "") or "").strip()
    if raw_tag and raw_tag != "SPEAKER_1":
        return raw_tag, 0.95, True

    c_start = float(getattr(chunk, "start", 0.0))
    c_end = max(c_start + 0.05, float(getattr(chunk, "end", c_start + 0.1)))
    turns = getattr(layout, "speaker_turns", None) or []
    if turns:
        totals: dict[str, float] = {}
        w_conf: dict[str, float] = {}
        for tr in turns:
            ov = min(c_end, float(tr.t_end)) - max(c_start, float(tr.t_start))
            if ov > 0.0:
                sid = tr.speaker_id
                totals[sid] = totals.get(sid, 0.0) + ov
                w_conf[sid] = w_conf.get(sid, 0.0) + ov * float(tr.confidence)
        if totals:
            ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            top_spk, top_dur = ranked[0]
            sec_dur = ranked[1][1] if len(ranked) > 1 else 0.0
            avg_conf = w_conf[top_spk] / max(1e-6, top_dur)
            chunk_len = max(0.08, c_end - c_start)
            clear_margin = sec_dur <= 0.0 or top_dur >= sec_dur * 1.15
            confident = (
                top_dur >= min(0.15, chunk_len * 0.35)
                and avg_conf >= 0.45
                and clear_margin
            )
            return top_spk, avg_conf, confident

    if not turns and shot is not None and len(shot.speaker_faces) > 1 and raw_tag:
        if any(a in shot.speaker_faces for a in _speaker_aliases(raw_tag)):
            return raw_tag, 0.90, True

    if shot is not None and shot.speaker_id:
        sc = float(shot.confidence or 0.75)
        return shot.speaker_id, sc, (sc >= 0.45 or len(shot.speaker_faces) <= 1)

    return raw_tag or "SPEAKER_1", 0.5, bool(not turns and raw_tag)


def _find_face_for_speaker(
    shot: Optional[ShotCropSegment],
    speaker_id: str,
) -> Optional[FaceAnchorBox]:
    if shot is None or not shot.speaker_faces:
        return None
    for alias in _speaker_aliases(speaker_id):
        if alias in shot.speaker_faces:
            return shot.speaker_faces[alias]
    if shot.speaker_id and any(a in _speaker_aliases(shot.speaker_id) for a in _speaker_aliases(speaker_id)):
        if shot.face_id and shot.face_id in shot.speaker_faces:
            return shot.speaker_faces[shot.face_id]
    if len(shot.speaker_faces) == 1:
        return next(iter(shot.speaker_faces.values()))
    return None


def _panel_for_face(
    face: FaceAnchorBox,
    config: ClipComposerConfig,
    shot: Optional[ShotCropSegment],
    cw: int,
    ch: int,
) -> tuple[CropBoxConfig, int, int, int, int]:
    """Return `(crop_box, dest_x, dest_y, target_w, target_h)` rendering `face` on canvas."""
    layout = config.layout
    top_crop = shot.top_crop if shot is not None else layout.top_crop
    bot_crop = (shot.bottom_crop if (shot is not None and shot.bottom_crop is not None) else layout.bottom_crop)

    eff_mode = layout.mode
    if layout.mode == "single_focus" and shot is not None and shot.shot_mode == "blurred_frame":
        eff_mode = "blurred_frame"
    elif getattr(layout, "interactive_reaction_cuts", False) and shot is not None and shot.shot_mode:
        eff_mode = shot.shot_mode

    if config.aspect_ratio == "16:9" or eff_mode == "single_focus":
        return top_crop, 0, 0, cw, ch

    if eff_mode == "blurred_frame":
        fg_ratio_map = {"1:1": (cw, cw), "4:3": (cw, int(round(cw * 0.75))), "16:9": (cw, int(round(cw * 9.0 / 16.0)))}
        fg_w, fg_h = fg_ratio_map.get(layout.blurred_frame_aspect, (cw, int(round(cw * 0.75))))
        fg_h = min(ch, _make_even(fg_h, 2, ch))
        return top_crop, 0, max(0, (ch - fg_h) // 2), fg_w, fg_h

    if eff_mode == "pip_facecam":
        px, py, pip_w, pip_h = compute_pip_rect(cw, ch, layout)
        return top_crop, px, py, pip_w, pip_h

    # split_screen or multi_speaker_split: pick whichever panel contains the face center
    geom = compute_split_geometry(cw, ch, layout.split_ratio_top, layout.divider.angle_deg)
    fcx = face.x + face.w * 0.5
    fcy = face.y + face.h * 0.5

    def _dist_score(c: CropBoxConfig) -> tuple[int, float]:
        inside = int(c.x <= fcx <= c.x + c.w and c.y <= fcy <= c.y + c.h)
        dx = fcx - (c.x + c.w * 0.5)
        dy = fcy - (c.y + c.h * 0.5)
        return inside, -(dx * dx + dy * dy)

    if _dist_score(top_crop) >= _dist_score(bot_crop):
        return top_crop, 0, 0, cw, geom.top_panel_h
    return bot_crop, 0, geom.bot_panel_y, cw, geom.bot_panel_h


def _project_face_to_canvas(
    face: FaceAnchorBox,
    config: ClipComposerConfig,
    shot: Optional[ShotCropSegment],
    src_w: int,
    src_h: int,
    cw: int,
    ch: int,
    rel_t: float,
    total_dur: float,
) -> Optional[tuple[float, float, float]]:
    """Project normalized source `face` to `(cx_canvas, head_top_canvas, face_h_canvas)` or `None`."""
    crop, dest_x, dest_y, target_w, target_h = _panel_for_face(face, config, shot, cw, ch)
    crop_w, crop_h, crop_x, crop_y = compute_pixel_crop_rect(crop, src_w, src_h, target_w, target_h)
    sw = max(2, int(src_w) - (int(src_w) % 2))
    sh = max(2, int(src_h) - (int(src_h) % 2))

    u = ((face.x + face.w * 0.5) * sw - crop_x) / max(1.0, float(crop_w))
    v_top = (face.y * sh - crop_y) / max(1.0, float(crop_h))
    v_bot = ((face.y + face.h) * sh - crop_y) / max(1.0, float(crop_h))

    if u < -0.10 or u > 1.10 or v_bot < -0.10 or v_top > 1.10:
        return None

    cx = dest_x + u * target_w
    head_top = dest_y + v_top * target_h
    face_h = max(16.0, (v_bot - v_top) * target_h)

    climax = config.overlays.climax_effects
    if climax.zoom_punch_enabled:
        wins = resolve_interactive_zoom_windows(config, max(total_dur, rel_t + 1.0))
        zf, ax, ay = evaluate_zoom_at_time(wins, rel_t)
        if zf > 1.001:
            ox = max(0.0, min(cw * (zf - 1.0), cw * zf * ax - cw * 0.5))
            oy = max(0.0, min(ch * (zf - 1.0), ch * zf * ay - ch * 0.5))
            cx = cx * zf - ox
            head_top = head_top * zf - oy
            face_h *= zf

    return cx, head_top, face_h


def _clamp_above_head(
    cx: float,
    head_top: float,
    face_h: float,
    gap_px: float,
    box_w: float,
    box_h: float,
    cw: int,
    ch: int,
) -> tuple[float, float]:
    hair_allowance = face_h * 0.16
    raw_y = head_top - hair_allowance - gap_px - box_h * 0.5

    half_w = min(float(box_w) * 0.5, cw * 0.44)
    min_x = max(32.0, half_w + 24.0)
    # Leave breathing room from Shorts right-edge action buttons on 9:16 when narrow
    right_pad = 104.0 if ch > cw else 36.0
    max_x = min(cw - right_pad, cw - half_w - 24.0)
    if min_x > max_x:
        min_x = max_x = cw * 0.5
    clamped_x = max(min_x, min(max_x, cx))

    min_y = max(round(ch * 0.075), box_h * 0.5 + 24.0)
    bottom_pad = 300.0 if ch > cw else 80.0
    max_y = min(ch - bottom_pad, ch * 0.80 - box_h * 0.5)
    if min_y > max_y:
        min_y = max_y = ch * 0.5
    clamped_y = max(min_y, min(max_y, raw_y))

    return round(clamped_x, 1), round(clamped_y, 1)


def resolve_chunks_anchor_positions(
    chunks: Sequence[Any],
    config: ClipComposerConfig,
    src_w: int = 1920,
    src_h: int = 1080,
    canvas_w: Optional[int] = None,
    canvas_h: Optional[int] = None,
    chunk_box_sizes: Optional[Sequence[tuple[float, float]]] = None,
) -> list[tuple[float, float]]:
    """Compute per-chunk `(pos_x, pos_y)` canvas anchors.

    For `placement_preset == 'above_speaker'`, places captions cleanly above the active
    speaker's head and snaps instantly at chunk boundaries, falling back to the last
    confident position on ambiguity or `bottom_safe` when no face is visible.
    """
    subs = config.subtitles
    cw = max(320, int(canvas_w or config.canvas_width))
    ch = max(320, int(canvas_h or config.canvas_height))

    if subs.placement_preset != "above_speaker":
        return [(float(subs.pos_x), float(subs.pos_y)) for _ in chunks]

    fallback_pos = (float(cw // 2), float(resolve_bottom_safe_y(cw, ch)))
    gap_px = float(getattr(subs, "above_speaker_gap_px", 40))
    total_dur = max(float(config.clip_duration_sec()), max((float(c.end) for c in chunks), default=1.0))

    positions: list[tuple[float, float]] = []
    last_confident_pos: Optional[tuple[float, float]] = None
    last_confident_shot_idx: Optional[int] = None

    for idx, chunk in enumerate(chunks):
        mid_t = 0.5 * (float(chunk.start) + float(chunk.end))
        shot = _shot_at_time(config.layout, mid_t)
        shot_idx = config.layout.shot_crops.index(shot) if (shot is not None and shot in config.layout.shot_crops) else None

        if shot is None or not shot.speaker_faces:
            # No face tracking info at this time -> safe bottom fallback (do not reuse stale face from another shot)
            last_confident_pos = None
            positions.append(fallback_pos)
            continue

        spk_id, _conf, is_confident = _resolve_chunk_speaker(chunk, config.layout, shot)
        face = _find_face_for_speaker(shot, spk_id)

        if not is_confident and len(shot.speaker_faces) > 1 and last_confident_pos is not None and last_confident_shot_idx == shot_idx:
            # Ambiguous crosstalk in a multi-person wide shot: hold last confident position (PRD Risiko G8)
            positions.append(last_confident_pos)
            continue

        if face is None:
            if last_confident_pos is not None and last_confident_shot_idx == shot_idx:
                positions.append(last_confident_pos)
            else:
                positions.append(fallback_pos)
            continue

        proj = _project_face_to_canvas(
            face=face,
            config=config,
            shot=shot,
            src_w=src_w,
            src_h=src_h,
            cw=cw,
            ch=ch,
            rel_t=mid_t,
            total_dur=total_dur,
        )
        if proj is None:
            positions.append(last_confident_pos if (last_confident_pos and last_confident_shot_idx == shot_idx) else fallback_pos)
            continue

        cx, head_top, face_h = proj
        if chunk_box_sizes and idx < len(chunk_box_sizes):
            bw, bh = chunk_box_sizes[idx]
        else:
            bw, bh = float(subs.font_size) * 4.5, float(subs.font_size) * 1.4

        pos = _clamp_above_head(cx, head_top, face_h, gap_px, bw, bh, cw, ch)
        if is_confident or len(shot.speaker_faces) == 1:
            last_confident_pos = pos
            last_confident_shot_idx = shot_idx
        positions.append(pos)

    return positions
