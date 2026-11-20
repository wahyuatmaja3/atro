"""Smart face-tracking, dual split-screen, and fit-all-blur 9:16 framing strategies."""

from __future__ import annotations

import asyncio
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from atro.modules.cutter.framing.base import FramingContext, FramingPlan, FramingStrategy
from atro.modules.cutter.framing.center_crop import CenterCrop9x16Strategy

ShotMode = Literal["single_focus", "split_screen", "fit_all_blur", "center_crop"]


@dataclass(slots=True)
class FaceBox:
    """Normalized [0..1] face bounding box in a sampled frame."""

    x: float
    y: float
    w: float
    h: float
    conf: float = 1.0

    @property
    def cx(self) -> float:
        return self.x + (self.w * 0.5)

    @property
    def cy(self) -> float:
        return self.y + (self.h * 0.5)

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h


@dataclass(slots=True)
class ShotSegmentPlan:
    """Computed framing parameters for one continuous shot within a clip."""

    t_start: float
    t_end: float
    mode: ShotMode
    centers: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]


def _make_even(val: float, minimum: int = 2, maximum: Optional[int] = None) -> int:
    ival = max(minimum, int(round(val)))
    ival -= ival % 2
    if ival < minimum:
        ival = minimum
    if maximum is not None:
        cap = max(minimum, maximum - (maximum % 2))
        ival = min(ival, cap)
    return ival


def _iou(a: FaceBox, b: FaceBox) -> float:
    ix1 = max(a.x, b.x)
    iy1 = max(a.y, b.y)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    union = (a.w * a.h) + (b.w * b.h) - inter
    return inter / max(1e-6, union)


def _nms_boxes(boxes: list[FaceBox], iou_thresh: float = 0.35) -> list[FaceBox]:
    if not boxes:
        return []
    ordered = sorted(boxes, key=lambda b: (b.conf, b.w * b.h), reverse=True)
    kept: list[FaceBox] = []
    for cand in ordered:
        if all(_iou(cand, prev) < iou_thresh for prev in kept):
            kept.append(cand)
    return kept


def _resolve_yunet_model_path() -> Optional[Path]:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "atro" / "assets" / "face_detection_yunet_2023mar.onnx")
    candidates.append(Path(__file__).resolve().parents[3] / "assets" / "face_detection_yunet_2023mar.onnx")
    for p in candidates:
        if p.is_file() and p.stat().st_size > 100_000:
            return p
    return None


def classify_shot_layout(
    observations: list[list[FaceBox]],
    frame_width: int,
    frame_height: int,
) -> tuple[ShotMode, list[tuple[float, float]], tuple[float, float, float, float]]:
    """Pure decision helper choosing single_focus, split_screen, fit_all_blur, or center_crop."""
    if not observations:
        return "center_crop", [(0.5, 0.4)], (0.0, 0.0, 1.0, 1.0)

    # Cluster faces by horizontal center across frames
    # Structure: [sum_cx, sum_cy, sum_x1, sum_y1, sum_x2, sum_y2, count, frame_indices_set]
    clusters: list[dict[str, Any]] = []
    for frame_idx, frame_faces in enumerate(observations):
        used_clusters: set[int] = set()
        for box in sorted(frame_faces, key=lambda b: b.cx):
            best_idx = -1
            best_dist = 0.14  # 14% horizontal tolerance
            for c_idx, cl in enumerate(clusters):
                if c_idx in used_clusters:
                    continue
                mean_cx = cl["sum_cx"] / cl["count"]
                dist = abs(box.cx - mean_cx)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = c_idx

            if best_idx >= 0:
                cl = clusters[best_idx]
                cl["sum_cx"] += box.cx
                cl["sum_cy"] += box.cy
                cl["sum_x1"] += box.x
                cl["sum_y1"] += box.y
                cl["sum_x2"] += box.x2
                cl["sum_y2"] += box.y2
                cl["count"] += 1
                cl["frames"].add(frame_idx)
                used_clusters.add(best_idx)
            else:
                clusters.append(
                    {
                        "sum_cx": box.cx,
                        "sum_cy": box.cy,
                        "sum_x1": box.x,
                        "sum_y1": box.y,
                        "sum_x2": box.x2,
                        "sum_y2": box.y2,
                        "count": 1,
                        "frames": {frame_idx},
                    }
                )
                used_clusters.add(len(clusters) - 1)

    if not clusters:
        return "center_crop", [(0.5, 0.4)], (0.0, 0.0, 1.0, 1.0)

    min_hits = 1 if len(observations) <= 2 else max(2, math.ceil(len(observations) * 0.25))
    persistent = [cl for cl in clusters if len(cl["frames"]) >= min_hits]
    if not persistent:
        persistent = [max(clusters, key=lambda c: len(c["frames"]))]

    # Sort left to right
    persistent.sort(key=lambda c: c["sum_cx"] / c["count"])

    centers: list[tuple[float, float]] = []
    min_x1, min_y1 = 1.0, 1.0
    max_x2, max_y2 = 0.0, 0.0
    for cl in persistent:
        cnt = cl["count"]
        cx = cl["sum_cx"] / cnt
        cy = cl["sum_cy"] / cnt
        x1 = cl["sum_x1"] / cnt
        y1 = cl["sum_y1"] / cnt
        x2 = cl["sum_x2"] / cnt
        y2 = cl["sum_y2"] / cnt
        centers.append((max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy))))
        min_x1 = min(min_x1, x1)
        min_y1 = min(min_y1, y1)
        max_x2 = max(max_x2, x2)
        max_y2 = max(max_y2, y2)

    bbox = (max(0.0, min_x1), max(0.0, min_y1), min(1.0, max_x2), min(1.0, max_y2))

    # Full 9:16 crop width fraction inside source frame
    crop_w_frac = (frame_height * (9.0 / 16.0)) / max(1, frame_width)
    outer_span = max(0.0, bbox[2] - bbox[0])

    if len(centers) == 1 or outer_span <= crop_w_frac * 0.88:
        mid_cx = sum(c[0] for c in centers) / len(centers)
        mid_cy = sum(c[1] for c in centers) / len(centers)
        return "single_focus", [(mid_cx, mid_cy)], bbox

    if len(centers) == 2 and abs(centers[1][0] - centers[0][0]) > 0.25:
        return "split_screen", centers, bbox

    return "fit_all_blur", centers, bbox


def _build_fit_all_blur_chain(
    in_label: str,
    out_label: str,
    src_w: int,
    src_h: int,
    bbox: tuple[float, float, float, float],
    idx_prefix: str = "fab",
) -> list[str]:
    """Generate filter_complex lines showing all subjects over a blurred 9:16 backdrop."""
    if (src_w / max(1, src_h)) <= (9.0 / 16.0):
        return [f"[{in_label}]scale=1080:1920:flags=lanczos,setsar=1[{out_label}]"]

    bg_cw, bg_ch, bg_cx, bg_cy = CenterCrop9x16Strategy.compute_crop_rect(src_w, src_h)

    # Determine horizontal foreground span covering all detected faces comfortably
    x1_norm, _, x2_norm, _ = bbox
    span_norm = max(0.0, x2_norm - x1_norm)
    if span_norm > 0.65 or (x1_norm == 0.0 and x2_norm == 1.0):
        fg_x = 0
        fg_w = _make_even(src_w, 2, src_w)
    else:
        margin = max(0.18, span_norm * 0.30)
        left_px = max(0, int(math.floor((x1_norm - margin) * src_w)))
        right_px = min(src_w, int(math.ceil((x2_norm + margin) * src_w)))
        min_fg_w = int(src_w * 0.62)
        if (right_px - left_px) < min_fg_w:
            mid = (left_px + right_px) // 2
            left_px = max(0, mid - min_fg_w // 2)
            right_px = min(src_w, left_px + min_fg_w)
            left_px = max(0, right_px - min_fg_w)
        fg_w = _make_even(right_px - left_px, 2, src_w)
        fg_x = max(0, min(src_w - fg_w, left_px))

    fg_h = _make_even(src_h, 2, src_h)
    bg_lbl = f"{idx_prefix}_bg"
    fg_lbl = f"{idx_prefix}_fg"
    bg_out = f"{idx_prefix}_bg_b"
    fg_out = f"{idx_prefix}_fg_s"

    return [
        f"[{in_label}]split=2[{bg_lbl}][{fg_lbl}]",
        (
            f"[{bg_lbl}]crop={bg_cw}:{bg_ch}:{bg_cx}:{bg_cy},"
            f"scale=1080:1920:flags=bilinear,boxblur=24:4,eq=brightness=-0.12,setsar=1[{bg_out}]"
        ),
        (
            f"[{fg_lbl}]crop={fg_w}:{fg_h}:{fg_x}:0,"
            f"scale=1080:-2:flags=lanczos,setsar=1[{fg_out}]"
        ),
        f"[{bg_out}][{fg_out}]overlay=x=(W-w)/2:y=(H-h)/2,setsar=1[{out_label}]",
    ]


def _build_shot_filter_lines(
    in_label: str,
    out_label: str,
    src_w: int,
    src_h: int,
    shot: ShotSegmentPlan,
    shot_idx: int,
) -> list[str]:
    """Build filter_complex lines for a single shot segment `[in_label]` -> `[out_label]`."""
    if shot.mode == "center_crop":
        cw, ch, cx, cy = CenterCrop9x16Strategy.compute_crop_rect(src_w, src_h)
        return [
            f"[{in_label}]crop={cw}:{ch}:{cx}:{cy},scale=1080:1920:flags=lanczos,setsar=1[{out_label}]"
        ]

    if shot.mode == "single_focus":
        cw, ch, _, _ = CenterCrop9x16Strategy.compute_crop_rect(src_w, src_h)
        cx_norm, cy_norm = shot.centers[0] if shot.centers else (0.5, 0.4)
        cx_px = int(round(cx_norm * src_w))
        cy_px = int(round(cy_norm * src_h))
        crop_x = max(0, min(src_w - cw, cx_px - cw // 2))
        crop_y = max(0, min(src_h - ch, cy_px - int(ch * 0.38)))
        return [
            f"[{in_label}]crop={cw}:{ch}:{crop_x}:{crop_y},scale=1080:1920:flags=lanczos,setsar=1[{out_label}]"
        ]

    if shot.mode == "split_screen" and len(shot.centers) >= 2:
        # Each stacked panel is 1080x960 (ratio 9:8 = 1.125)
        hw = _make_even(min(src_w, max(src_h * (9.0 / 16.0), src_w * 0.52)), 2, src_w)
        hh = _make_even(hw * (8.0 / 9.0), 2, src_h)
        hw = _make_even(min(hw, hh * (9.0 / 8.0)), 2, src_w)

        (cx1, cy1), (cx2, cy2) = shot.centers[0], shot.centers[1]
        x1 = max(0, min(src_w - hw, int(round(cx1 * src_w)) - hw // 2))
        y1 = max(0, min(src_h - hh, int(round(cy1 * src_h)) - int(hh * 0.38)))
        x2 = max(0, min(src_w - hw, int(round(cx2 * src_w)) - hw // 2))
        y2 = max(0, min(src_h - hh, int(round(cy2 * src_h)) - int(hh * 0.38)))

        s_top = f"sp{shot_idx}_top_in"
        s_bot = f"sp{shot_idx}_bot_in"
        p_top = f"sp{shot_idx}_top"
        p_bot = f"sp{shot_idx}_bot"
        return [
            f"[{in_label}]split=2[{s_top}][{s_bot}]",
            f"[{s_top}]crop={hw}:{hh}:{x1}:{y1},scale=1080:960:flags=lanczos,setsar=1[{p_top}]",
            f"[{s_bot}]crop={hw}:{hh}:{x2}:{y2},scale=1080:960:flags=lanczos,setsar=1[{p_bot}]",
            (
                f"[{p_top}][{p_bot}]vstack=inputs=2,"
                f"drawbox=x=0:y=958:w=1080:h=4:color=#0F1117:t=fill,setsar=1[{out_label}]"
            ),
        ]

    # fit_all_blur
    return _build_fit_all_blur_chain(
        in_label=in_label,
        out_label=out_label,
        src_w=src_w,
        src_h=src_h,
        bbox=shot.bbox,
        idx_prefix=f"fab{shot_idx}",
    )


def _assemble_framing_plan_from_shots(
    shots: list[ShotSegmentPlan],
    src_w: int,
    src_h: int,
    fps: float,
) -> FramingPlan:
    if not shots:
        cw, ch, cx, cy = CenterCrop9x16Strategy.compute_crop_rect(src_w, src_h)
        return FramingPlan(
            filter_lines=[f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale=1080:1920:flags=lanczos,setsar=1[framed]"],
            output_label="framed",
            output_width=1080,
            output_height=1920,
            summary="center_crop",
        )

    if len(shots) == 1:
        lines = _build_shot_filter_lines("0:v", "framed", src_w, src_h, shots[0], shot_idx=0)
        return FramingPlan(
            filter_lines=lines,
            output_label="framed",
            output_width=1080,
            output_height=1920,
            summary=f"1x {shots[0].mode}",
        )

    fps_safe = max(10.0, min(120.0, float(fps or 30.0)))
    split_labels = "".join(f"[ss{i}]" for i in range(len(shots)))
    lines: list[str] = [f"[0:v]fps={fps_safe:.3f},split={len(shots)}{split_labels}"]
    concat_inputs: list[str] = []

    for i, shot in enumerate(shots):
        trim_lbl = f"st{i}"
        out_lbl = f"sv{i}"
        lines.append(
            f"[ss{i}]trim=start={shot.t_start:.3f}:end={shot.t_end:.3f},setpts=PTS-STARTPTS[{trim_lbl}]"
        )
        lines.extend(_build_shot_filter_lines(trim_lbl, out_lbl, src_w, src_h, shot, shot_idx=i))
        concat_inputs.append(f"[{out_lbl}]")

    lines.append(f"{''.join(concat_inputs)}concat=n={len(shots)}:v=1:a=0,setsar=1[framed]")
    modes_summary = ", ".join(dict.fromkeys(s.mode for s in shots))
    return FramingPlan(
        filter_lines=lines,
        output_label="framed",
        output_width=1080,
        output_height=1920,
        summary=f"{len(shots)} shot ({modes_summary})",
    )


def _analyze_shots_sync(ctx: FramingContext) -> list[ShotSegmentPlan]:
    """Sample frames in [ctx.start_sec, ctx.end_sec], detect scene cuts and faces."""
    import cv2

    duration = max(0.5, ctx.end_sec - ctx.start_sec)
    cap = cv2.VideoCapture(str(ctx.source_path))
    if not cap.isOpened():
        return []

    try:
        yunet_path = _resolve_yunet_model_path()
        yunet = None
        if yunet_path is not None:
            try:
                yunet = cv2.FaceDetectorYN.create(
                    str(yunet_path),
                    "",
                    (320, 240),
                    score_threshold=0.62,
                    nms_threshold=0.30,
                )
            except Exception:
                yunet = None

        cascade_dir = Path(cv2.data.haarcascades)
        frontal_path = cascade_dir / "haarcascade_frontalface_alt2.xml"
        if not frontal_path.is_file():
            frontal_path = cascade_dir / "haarcascade_frontalface_default.xml"
        profile_path = cascade_dir / "haarcascade_profileface.xml"
        frontal_clf = cv2.CascadeClassifier(str(frontal_path)) if frontal_path.is_file() else None
        profile_clf = cv2.CascadeClassifier(str(profile_path)) if profile_path.is_file() else None

        # Sample ~2 FPS, capped between 4 and 90 samples per clip for fast runtime
        sample_step = 0.5
        if duration / sample_step > 90:
            sample_step = duration / 90.0
        timestamps: list[float] = []
        t = 0.0
        while t < duration - 0.15:
            timestamps.append(round(t, 3))
            t += sample_step
        if not timestamps:
            timestamps.append(0.0)

        samples: list[tuple[float, Any, list[FaceBox]]] = []
        for rel_t in timestamps:
            abs_ms = (ctx.start_sec + rel_t) * 1000.0
            cap.set(cv2.CAP_PROP_POS_MSEC, abs_ms)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            fh, fw = frame.shape[:2]
            if fw > 640:
                scale = 640.0 / fw
                small = cv2.resize(frame, (640, max(2, int(round(fh * scale)))), interpolation=cv2.INTER_AREA)
            else:
                small = frame
            sh, sw = small.shape[:2]

            # HSV histogram for scene-cut detection
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)

            boxes: list[FaceBox] = []
            if yunet is not None:
                yunet.setInputSize((sw, sh))
                _, faces_arr = yunet.detect(small)
                if faces_arr is not None:
                    for row in faces_arr:
                        bx, by, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                        conf = float(row[-1]) if len(row) > 4 else 0.9
                        if bw / sw >= 0.035 and bh / sh >= 0.035:
                            boxes.append(
                                FaceBox(
                                    x=max(0.0, bx / sw),
                                    y=max(0.0, by / sh),
                                    w=min(1.0, bw / sw),
                                    h=min(1.0, bh / sh),
                                    conf=conf,
                                )
                            )
            else:
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                gray = cv2.equalizeHist(gray)
                min_side = max(24, int(round(sw * 0.04)))
                raw_boxes: list[FaceBox] = []
                if frontal_clf is not None and not frontal_clf.empty():
                    for x, y, w, h in frontal_clf.detectMultiScale(gray, 1.15, 4, minSize=(min_side, min_side)):
                        raw_boxes.append(FaceBox(x / sw, y / sh, w / sw, h / sh, conf=0.9))
                if profile_clf is not None and not profile_clf.empty():
                    for x, y, w, h in profile_clf.detectMultiScale(gray, 1.15, 4, minSize=(min_side, min_side)):
                        raw_boxes.append(FaceBox(x / sw, y / sh, w / sw, h / sh, conf=0.75))
                    flipped = cv2.flip(gray, 1)
                    for x, y, w, h in profile_clf.detectMultiScale(flipped, 1.15, 4, minSize=(min_side, min_side)):
                        orig_x = (sw - x - w) / sw
                        raw_boxes.append(FaceBox(orig_x, y / sh, w / sw, h / sh, conf=0.75))
                boxes = _nms_boxes(raw_boxes, iou_thresh=0.35)

            samples.append((rel_t, hist, boxes))

        if not samples:
            return []

        # Partition into raw shots via HSV correlation scene-cut detection
        raw_groups: list[list[tuple[float, list[FaceBox]]]] = [[(samples[0][0], samples[0][2])]]
        for idx in range(1, len(samples)):
            prev_hist = samples[idx - 1][1]
            cur_t, cur_hist, cur_boxes = samples[idx]
            corr = cv2.compareHist(prev_hist, cur_hist, cv2.HISTCMP_CORREL)
            if corr < 0.65:
                raw_groups.append([(cur_t, cur_boxes)])
            else:
                raw_groups[-1].append((cur_t, cur_boxes))

        # Convert groups into continuous time segments [t_start, t_end] covering 0..duration
        raw_shots: list[ShotSegmentPlan] = []
        for g_idx, grp in enumerate(raw_groups):
            t_start = 0.0 if g_idx == 0 else grp[0][0]
            t_end = duration if g_idx == len(raw_groups) - 1 else raw_groups[g_idx + 1][0][0]
            obs = [item[1] for item in grp]
            mode, centers, bbox = classify_shot_layout(obs, ctx.source_width, ctx.source_height)
            raw_shots.append(
                ShotSegmentPlan(
                    t_start=round(t_start, 3),
                    t_end=round(t_end, 3),
                    mode=mode,
                    centers=centers,
                    bbox=bbox,
                )
            )

        # Merge ultra-short (< 1.0s) shots into neighbor
        merged: list[ShotSegmentPlan] = []
        for shot in raw_shots:
            if merged and (shot.t_end - shot.t_start) < 1.0:
                merged[-1].t_end = shot.t_end
            else:
                merged.append(shot)

        if len(merged) >= 2 and (merged[0].t_end - merged[0].t_start) < 1.0:
            merged[1].t_start = merged[0].t_start
            merged.pop(0)

        # Merge adjacent shots with identical mode and similar face centers
        compacted: list[ShotSegmentPlan] = []
        for shot in merged:
            if compacted and compacted[-1].mode == shot.mode and len(compacted[-1].centers) == len(shot.centers):
                max_shift = max(
                    abs(c1[0] - c2[0]) for c1, c2 in zip(compacted[-1].centers, shot.centers)
                ) if shot.centers else 0.0
                if max_shift < 0.08:
                    prev = compacted[-1]
                    total_dur = max(0.1, (prev.t_end - prev.t_start) + (shot.t_end - shot.t_start))
                    w_prev = (prev.t_end - prev.t_start) / total_dur
                    w_cur = (shot.t_end - shot.t_start) / total_dur
                    prev.centers = [
                        (c1[0] * w_prev + c2[0] * w_cur, c1[1] * w_prev + c2[1] * w_cur)
                        for c1, c2 in zip(prev.centers, shot.centers)
                    ]
                    prev.bbox = (
                        min(prev.bbox[0], shot.bbox[0]),
                        min(prev.bbox[1], shot.bbox[1]),
                        max(prev.bbox[2], shot.bbox[2]),
                        max(prev.bbox[3], shot.bbox[3]),
                    )
                    prev.t_end = shot.t_end
                    continue
            compacted.append(shot)

        # Cap to max 10 shots to keep filtergraph fast and compact
        while len(compacted) > 10:
            shortest_idx = min(range(len(compacted)), key=lambda i: compacted[i].t_end - compacted[i].t_start)
            if shortest_idx > 0:
                compacted[shortest_idx - 1].t_end = compacted[shortest_idx].t_end
                compacted.pop(shortest_idx)
            else:
                compacted[1].t_start = compacted[0].t_start
                compacted.pop(0)

        if compacted:
            compacted[0].t_start = 0.0
            compacted[-1].t_end = round(duration, 3)

        return compacted
    finally:
        cap.release()


class FitAllBlur9x16Strategy(FramingStrategy):
    """Fits full landscape subject frame inside 9:16 over a blurred backdrop."""

    name = "fit_all_blur"

    async def build_video_filter(self, ctx: FramingContext) -> str:
        lines = _build_fit_all_blur_chain(
            in_label="0:v",
            out_label="framed",
            src_w=ctx.source_width,
            src_h=ctx.source_height,
            bbox=(0.0, 0.0, 1.0, 1.0),
            idx_prefix="fab",
        )
        return ";".join(lines)

    async def build_framing_plan(self, ctx: FramingContext) -> FramingPlan:
        lines = _build_fit_all_blur_chain(
            in_label="0:v",
            out_label="framed",
            src_w=ctx.source_width,
            src_h=ctx.source_height,
            bbox=(0.0, 0.0, 1.0, 1.0),
            idx_prefix="fab",
        )
        return FramingPlan(
            filter_lines=lines,
            output_label="framed",
            output_width=1080,
            output_height=1920,
            summary="fit_all_blur",
        )


class FaceTracking9x16Strategy(FramingStrategy):
    """Smart face-tracking 9:16 strategy with single focus, dual split-screen, and fit-all-blur."""

    name = "smart_face"

    def __init__(self) -> None:
        self._fallback = CenterCrop9x16Strategy()

    async def detect_focus_keyframes(self, ctx: FramingContext) -> list[tuple[float, float]]:
        """Sample clip frames and return `(rel_timestamp_sec, normalized_center_x)`."""
        if not ctx.source_path.is_file():
            return []
        shots = await asyncio.to_thread(_analyze_shots_sync, ctx)
        return [
            (shot.t_start, shot.centers[0][0] if shot.centers else 0.5)
            for shot in shots
        ]

    async def build_video_filter(self, ctx: FramingContext) -> str:
        """Build a single-stream -vf fallback filter when called outside filter_complex."""
        if not ctx.source_path.is_file():
            return await self._fallback.build_video_filter(ctx)

        shots = await asyncio.to_thread(_analyze_shots_sync, ctx)
        if not shots:
            return await self._fallback.build_video_filter(ctx)

        cw, ch, _, _ = CenterCrop9x16Strategy.compute_crop_rect(ctx.source_width, ctx.source_height)
        first = shots[0]
        cx_norm = first.centers[0][0] if first.centers else 0.5
        crop_x = max(0, min(ctx.source_width - cw, int(round(cx_norm * ctx.source_width)) - cw // 2))
        return f"crop={cw}:{ch}:{crop_x}:0,scale=1080:1920:flags=lanczos,setsar=1"

    async def build_framing_plan(self, ctx: FramingContext) -> FramingPlan:
        """Analyze faces & scene cuts to build multi-shot smart 9:16 FramingPlan."""
        if not ctx.source_path.is_file():
            return await self._fallback.build_framing_plan(ctx)

        shots = await asyncio.to_thread(_analyze_shots_sync, ctx)
        if not shots:
            return await self._fallback.build_framing_plan(ctx)

        return _assemble_framing_plan_from_shots(
            shots=shots,
            src_w=ctx.source_width,
            src_h=ctx.source_height,
            fps=ctx.fps,
        )
