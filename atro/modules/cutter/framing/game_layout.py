"""Modern 9:16 Game Shorts layout: 40% streamer facecam (top) + 60% gameplay (bottom)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from atro.modules.cutter.framing.base import FramingContext, FramingPlan, FramingStrategy
from atro.modules.cutter.framing.face_tracking import (
    FaceBox,
    _build_fit_all_blur_chain,
    _make_even,
    _nms_boxes,
    _resolve_yunet_model_path,
)


def _pick_dominant_streamer_face(observations: list[list[FaceBox]]) -> Optional[FaceBox]:
    """Select the most temporally persistent face cluster across sampled frames."""
    clusters: list[dict[str, float]] = []
    for frame_faces in observations:
        used: set[int] = set()
        for box in frame_faces:
            best_idx = -1
            best_dist = 0.16
            for idx, cl in enumerate(clusters):
                if idx in used:
                    continue
                cnt = cl["count"]
                dist = abs(box.cx - (cl["sum_cx"] / cnt)) + abs(box.cy - (cl["sum_cy"] / cnt))
                if dist < best_dist:
                    best_dist = dist
                    best_idx = idx
            if best_idx >= 0:
                cl = clusters[best_idx]
                cl["sum_x"] += box.x
                cl["sum_y"] += box.y
                cl["sum_w"] += box.w
                cl["sum_h"] += box.h
                cl["sum_cx"] += box.cx
                cl["sum_cy"] += box.cy
                cl["count"] += 1.0
                used.add(best_idx)
            else:
                clusters.append(
                    {
                        "sum_x": box.x,
                        "sum_y": box.y,
                        "sum_w": box.w,
                        "sum_h": box.h,
                        "sum_cx": box.cx,
                        "sum_cy": box.cy,
                        "count": 1.0,
                    }
                )
                used.add(len(clusters) - 1)

    if not clusters:
        return None

    best = max(clusters, key=lambda c: (c["count"], c["sum_w"] * c["sum_h"]))
    cnt = best["count"]
    return FaceBox(
        x=max(0.0, best["sum_x"] / cnt),
        y=max(0.0, best["sum_y"] / cnt),
        w=min(1.0, best["sum_w"] / cnt),
        h=min(1.0, best["sum_h"] / cnt),
    )


def _detect_streamer_face_sync(ctx: FramingContext) -> Optional[FaceBox]:
    """Sample ~12 frames across `[ctx.start_sec, ctx.end_sec]` to locate the streamer's webcam face."""
    if not ctx.source_path.is_file():
        return None

    import cv2

    cap = cv2.VideoCapture(str(ctx.source_path))
    if not cap.isOpened():
        return None

    try:
        yunet_path = _resolve_yunet_model_path()
        yunet = None
        if yunet_path is not None:
            try:
                yunet = cv2.FaceDetectorYN.create(
                    str(yunet_path),
                    "",
                    (320, 240),
                    score_threshold=0.58,
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

        duration = max(0.5, ctx.end_sec - ctx.start_sec)
        sample_count = 12
        step = duration / sample_count
        observations: list[list[FaceBox]] = []

        for idx in range(sample_count):
            rel_t = min(duration - 0.1, (idx + 0.5) * step)
            abs_ms = max(0.0, (ctx.start_sec + rel_t) * 1000.0)
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

            boxes: list[FaceBox] = []
            if yunet is not None:
                yunet.setInputSize((sw, sh))
                _, faces_arr = yunet.detect(small)
                if faces_arr is not None:
                    for row in faces_arr:
                        bx, by, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                        conf = float(row[-1]) if len(row) > 4 else 0.9
                        if bw / sw >= 0.03 and bh / sh >= 0.03:
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
                min_side = max(20, int(round(sw * 0.035)))
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

            if boxes:
                observations.append(boxes)

        return _pick_dominant_streamer_face(observations)
    finally:
        cap.release()


def build_game_split_filter_lines(
    src_w: int,
    src_h: int,
    face_box: Optional[FaceBox],
    in_label: str = "0:v",
    out_label: str = "framed",
) -> tuple[list[str], str]:
    """Build 40% facecam (top) + 60% gameplay (bottom) filter lines, or fit-all-blur if no face."""
    if face_box is None:
        lines = _build_fit_all_blur_chain(
            in_label=in_label,
            out_label=out_label,
            src_w=src_w,
            src_h=src_h,
            bbox=(0.0, 0.0, 1.0, 1.0),
            idx_prefix="gmb",
        )
        return lines, "game_nocam (fit_all_blur)"

    # Top panel: 1080 x 768 (40% height, ratio 1.40625) focused around streamer face + shoulders
    top_ratio = 1080.0 / 768.0
    cam_h = _make_even(max(src_h * 0.24, min(src_h * 0.48, face_box.h * src_h * 3.0)), 2, src_h)
    cam_w = _make_even(cam_h * top_ratio, 2, src_w)
    if cam_w >= src_w - 2 or (cam_w / max(1, cam_h)) < (top_ratio * 0.96):
        cam_h = _make_even(cam_w / top_ratio, 2, src_h)

    face_cx_px = int(round(face_box.cx * src_w))
    face_cy_px = int(round(face_box.cy * src_h))
    cam_x = max(0, min(src_w - cam_w, face_cx_px - cam_w // 2))
    cam_y = max(0, min(src_h - cam_h, face_cy_px - int(cam_h * 0.38)))

    # Bottom panel: 1080 x 1152 (60% height, ratio 0.9375) centered on gameplay action
    gp_ratio = 1080.0 / 1152.0
    gp_h = _make_even(src_h, 2, src_h)
    gp_w = _make_even(gp_h * gp_ratio, 2, src_w)
    if gp_w >= src_w - 2 and (src_w / max(1, src_h)) < gp_ratio:
        gp_w = _make_even(src_w, 2, src_w)
        gp_h = _make_even(gp_w / gp_ratio, 2, src_h)
    gp_x = max(0, (src_w - gp_w) // 2)
    gp_y = max(0, (src_h - gp_h) // 2)

    lines = [
        f"[{in_label}]split=2[gm_cam_in][gm_gp_in]",
        f"[gm_cam_in]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},scale=1080:768:flags=lanczos,setsar=1[gm_cam]",
        f"[gm_gp_in]crop={gp_w}:{gp_h}:{gp_x}:{gp_y},scale=1080:1152:flags=lanczos,setsar=1[gm_gp]",
        (
            f"[gm_cam][gm_gp]vstack=inputs=2,"
            f"drawbox=x=0:y=764:w=1080:h=8:color=#0B0F19:t=fill,"
            f"drawbox=x=0:y=767:w=1080:h=3:color=#38BDF8@0.92:t=fill,"
            f"setsar=1[{out_label}]"
        ),
    ]
    return lines, "game_split (facecam 40% + gameplay 60%)"


class GameLayout9x16Strategy(FramingStrategy):
    """9:16 Game Shorts strategy placing detected facecam on top (40%) and gameplay below (60%)."""

    name = "game_split"

    async def build_video_filter(self, ctx: FramingContext) -> str:
        face_box = await asyncio.to_thread(_detect_streamer_face_sync, ctx)
        lines, _ = build_game_split_filter_lines(ctx.source_width, ctx.source_height, face_box)
        return ";".join(lines)

    async def build_framing_plan(self, ctx: FramingContext) -> FramingPlan:
        face_box = await asyncio.to_thread(_detect_streamer_face_sync, ctx)
        lines, summary = build_game_split_filter_lines(ctx.source_width, ctx.source_height, face_box)
        return FramingPlan(
            filter_lines=lines,
            output_label="framed",
            output_width=1080,
            output_height=1920,
            summary=summary,
        )
