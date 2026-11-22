"""Context- & Point-of-Interest-aware podcast shot planner (multi-angle, 5-speaker)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from atro.core.composer_schema import (
    ComposerLayoutMode,
    CropBoxConfig,
    LayoutLayerConfig,
    ShotCropSegment,
    ShotReason,
)
from atro.core.schemas import FocusHintItem, TranscriptSegment
from atro.modules.cutter.framing import face_tracking as ft_mod
from atro.modules.cutter.framing.diarization import (
    SpeakerFaceBinding,
    SpeakerSegment,
    active_speakers_in_range,
    diarize_audio_window_sync,
    load_job_speaker_map,
    save_job_speaker_map,
)
from atro.modules.cutter.framing.face_identity import (
    DetectedFaceObs,
    FaceIdentityTracker,
    detect_faces_with_features,
    load_sface_recognizer,
)


@dataclass(slots=True)
class _SampleFrame:
    rel_t: float
    abs_t: float
    angle_id: int
    is_cut: bool
    faces: list[DetectedFaceObs]


def _make_single_crop(cx: float, cy: float, w: float = 0.34, h: float = 1.0, label: str = "Pembicara") -> CropBoxConfig:
    tx = max(0.0, min(1.0 - w, cx - w * 0.5))
    ty = max(0.0, min(1.0 - h, cy - h * 0.42))
    return CropBoxConfig(
        auto_detect=True,
        x=round(tx, 4),
        y=round(ty, 4),
        w=round(w, 4),
        h=round(h, 4),
        label=label,
    )


def _make_group_crop(faces: list[DetectedFaceObs], label: str = "Grup Podcast") -> CropBoxConfig:
    if not faces:
        return CropBoxConfig(auto_detect=True, x=0.14, y=0.0, w=0.72, h=0.96, label=label)
    min_x = min(f.box.x for f in faces)
    max_x = max(f.box.x2 for f in faces)
    min_y = min(f.box.y for f in faces)
    max_y = max(f.box.y2 for f in faces)
    span_w = max(0.42, min(0.96, (max_x - min_x) + 0.22))
    span_h = max(0.55, min(0.98, (max_y - min_y) + 0.42))
    mid_x = (min_x + max_x) * 0.5
    mid_y = (min_y + max_y) * 0.5
    gx = max(0.0, min(1.0 - span_w, mid_x - span_w * 0.5))
    gy = max(0.0, min(1.0 - span_h, mid_y - span_h * 0.40))
    return CropBoxConfig(
        auto_detect=True,
        x=round(gx, 4),
        y=round(gy, 4),
        w=round(span_w, 4),
        h=round(span_h, 4),
        label=label,
    )


def _match_prev_activity(
    gray_cur: np.ndarray,
    gray_prev: Optional[np.ndarray],
    obs: DetectedFaceObs,
    prev_obs: list[DetectedFaceObs],
    is_cut: bool,
) -> float:
    if is_cut or gray_prev is None or not prev_obs:
        return 0.0
    prev_boxes = [p.box for p in prev_obs]
    return ft_mod._measure_face_speaking_activity(gray_cur, gray_prev, obs.box, prev_boxes)


def _sample_video_observations(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    fps_hint: float,
) -> list[_SampleFrame]:
    """Sample frames cleanly with YuNet+SFace face identities, cuts, and mouth activity."""
    duration = max(0.5, end_sec - start_sec)
    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        return []

    try:
        yunet_path = ft_mod._resolve_yunet_model_path()
        yunet = None
        if yunet_path is not None:
            try:
                yunet = cv2.FaceDetectorYN.create(
                    str(yunet_path),
                    "",
                    (320, 180),
                    score_threshold=0.48,
                    nms_threshold=0.30,
                )
            except Exception:
                yunet = None
        sface = load_sface_recognizer()

        cascade_dir = Path(cv2.data.haarcascades)
        frontal_path = cascade_dir / "haarcascade_frontalface_alt2.xml"
        if not frontal_path.is_file():
            frontal_path = cascade_dir / "haarcascade_frontalface_default.xml"
        profile_path = cascade_dir / "haarcascade_profileface.xml"
        frontal_clf = cv2.CascadeClassifier(str(frontal_path)) if frontal_path.is_file() else None
        profile_clf = cv2.CascadeClassifier(str(profile_path)) if profile_path.is_file() else None

        src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if src_fps <= 1.0 or src_fps > 120.0:
            src_fps = max(10.0, min(60.0, float(fps_hint or 30.0)))

        grab_skip = 1 if duration <= 20.0 else max(1, min(3, int(round(src_fps / 10.0))))
        step_dt = (grab_skip + 1) / src_fps

        if start_sec > 0.05:
            cap.set(cv2.CAP_PROP_POS_MSEC, start_sec * 1000.0)

        tracker = FaceIdentityTracker()
        samples: list[_SampleFrame] = []
        prev_grid = None
        prev_hist = None
        prev_gray = None
        prev_obs: list[DetectedFaceObs] = []
        prev_t_seen = -1.0
        angle_id = 0
        step_idx = 0
        max_steps = int(math.ceil(duration / max(0.02, step_dt))) + 20

        while step_idx < max_steps:
            ok, frame = cap.read()
            if not ok or frame is None:
                break

            pos_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            est_t = round(step_idx * step_dt, 3)
            if pos_ms > 0.0 and (pos_ms / 1000.0) + 0.5 >= start_sec:
                rel_t = round(max(0.0, (pos_ms / 1000.0) - start_sec), 3)
            else:
                rel_t = est_t
            if rel_t > duration + 0.08:
                break
            rel_t = min(duration, rel_t)

            small = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
            grid = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA).astype(np.float32)

            luma_diff = 0.0
            corr = 1.0
            is_cut = False
            if step_idx > 0 and prev_grid is not None and prev_hist is not None:
                corr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
                luma_diff = float(np.mean(np.abs(grid - prev_grid))) / 255.0
                if corr < 0.72 or luma_diff > 0.13 or (corr < 0.86 and luma_diff > 0.085):
                    is_cut = True

            # Re-run detector on cut or ~1.0s cadence (faster on 20-60s clips without missing speakers)
            need_detect = (
                is_cut
                or step_idx == 0
                or not prev_obs
                or (rel_t - prev_t_seen) >= 0.45
            )
            if need_detect:
                cur_obs = detect_faces_with_features(
                    small,
                    gray,
                    yunet,
                    sface,
                    frontal_clf,
                    profile_clf,
                    run_haar_fallback=(is_cut or step_idx % 2 == 0 or not prev_obs),
                )
                prev_t_seen = rel_t
                if not is_cut and step_idx > 0 and (luma_diff > 0.042 or corr < 0.94) and cur_obs and prev_obs:
                    p_cx = ft_mod._primary_face_cx([p.box for p in prev_obs])
                    c_cx = ft_mod._primary_face_cx([c.box for c in cur_obs])
                    if len(cur_obs) != len(prev_obs) or (p_cx is not None and c_cx is not None and abs(c_cx - p_cx) > 0.14):
                        is_cut = True
            else:
                cur_obs = [
                    DetectedFaceObs(
                        box=ft_mod.FaceBox(p.box.x, p.box.y, p.box.w, p.box.h, p.box.conf, 0.0),
                        sface_vec=p.sface_vec,
                        app_vec=p.app_vec,
                        face_id=p.face_id,
                    )
                    for p in prev_obs
                ]

            if is_cut:
                angle_id += 1

            tracker.assign_frame(cur_obs, angle_id=angle_id, is_cut=is_cut)
            for obs in cur_obs:
                obs.box.activity = _match_prev_activity(gray, prev_gray, obs, prev_obs, is_cut)

            samples.append(
                _SampleFrame(
                    rel_t=rel_t,
                    abs_t=round(start_sec + rel_t, 3),
                    angle_id=angle_id,
                    is_cut=is_cut,
                    faces=cur_obs,
                )
            )

            prev_grid = grid
            prev_hist = hist
            prev_gray = gray
            prev_obs = cur_obs
            step_idx += 1

            grab_ok = True
            for _ in range(grab_skip):
                if not cap.grab():
                    grab_ok = False
                    break
            if not grab_ok:
                break

        return samples
    finally:
        cap.release()


def correlate_speakers_and_faces(
    samples: list[_SampleFrame],
    diar_segments: list[SpeakerSegment],
) -> tuple[dict[str, str], dict[str, float]]:
    """Learn 1-to-1 `speaker_id -> face_id` map using solo shots & mouth activity."""
    if not samples or not diar_segments:
        return {}, {}

    score_mat: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}

    for s in samples:
        if not s.faces:
            continue
        spks = active_speakers_in_range(diar_segments, s.abs_t - 0.12, s.abs_t + 0.12)
        if not spks:
            continue
        dom_spk, spk_dur = max(spks.items(), key=lambda kv: kv[1])
        total_spk = sum(spks.values())
        if spk_dur < total_spk * 0.65:
            continue

        if len(s.faces) == 1:
            # Close-up / single visible face during single speaker's speech = strongest link!
            f = s.faces[0]
            key = (dom_spk, f.face_id)
            score_mat[key] = score_mat.get(key, 0.0) + 2.0 + min(2.0, f.box.activity * 0.35)
            counts[key] = counts.get(key, 0) + 1
        else:
            ranked = sorted(s.faces, key=lambda x: x.box.activity, reverse=True)
            best_f = ranked[0]
            sec_act = ranked[1].box.activity if len(ranked) > 1 else 0.0
            if best_f.box.activity >= 1.3 and best_f.box.activity >= sec_act * 1.25:
                key = (dom_spk, best_f.face_id)
                score_mat[key] = score_mat.get(key, 0.0) + 1.1 + min(2.5, best_f.box.activity * 0.30)
                counts[key] = counts.get(key, 0) + 1

    # Greedy bipartite 1-to-1 assignment (highest confidence first)
    pairs = sorted(score_mat.items(), key=lambda kv: kv[1], reverse=True)
    spk_to_face: dict[str, str] = {}
    spk_conf: dict[str, float] = {}
    used_faces: set[str] = set()

    spk_totals: dict[str, float] = {}
    for (spk, _), val in pairs:
        spk_totals[spk] = spk_totals.get(spk, 0.0) + val

    for (spk, fid), val in pairs:
        if spk in spk_to_face or fid in used_faces:
            continue
        if counts.get((spk, fid), 0) < 2 and val < 2.5:
            continue
        spk_to_face[spk] = fid
        used_faces.add(fid)
        denom = max(1.0, spk_totals.get(spk, val))
        spk_conf[spk] = round(min(1.0, (val / denom) * 0.85 + 0.15), 3)

    return spk_to_face, spk_conf


def _active_hint_at(
    focus_hints: list[FocusHintItem],
    abs_t: float,
) -> Optional[FocusHintItem]:
    for h in focus_hints:
        if h.t_start <= abs_t <= h.t_end:
            return h
    return None


def _rolling_mouth_scores(samples: list[_SampleFrame], idx: int, half_win: float = 0.50) -> dict[str, float]:
    ti = samples[idx].rel_t
    sums: dict[str, float] = {}
    cnts: dict[str, int] = {}
    lo = idx
    while lo > 0 and (ti - samples[lo - 1].rel_t) <= half_win:
        lo -= 1
    hi = idx
    while hi + 1 < len(samples) and (samples[hi + 1].rel_t - ti) <= half_win:
        hi += 1
    for k in range(lo, hi + 1):
        for f in samples[k].faces:
            sums[f.face_id] = sums.get(f.face_id, 0.0) + f.box.activity
            cnts[f.face_id] = cnts.get(f.face_id, 0) + 1
    return {fid: sums[fid] / max(1, cnts[fid]) for fid in sums}


def plan_podcast_shots_from_observations(
    samples: list[_SampleFrame],
    duration: float,
    layout: LayoutLayerConfig,
    diar_segments: list[SpeakerSegment],
    focus_hints: Optional[list[FocusHintItem]] = None,
    mode: Optional[ComposerLayoutMode] = None,
) -> list[ShotCropSegment]:
    """Pure decision engine converting sampled frames + audio diarization + POI into smooth shots."""
    if not samples:
        return []

    eff_mode = mode or layout.mode
    hints = list(focus_hints or [])
    spk_to_face, spk_conf = correlate_speakers_and_faces(samples, diar_segments)
    face_to_spk = {v: k for k, v in spk_to_face.items()}

    min_shot = max(0.8, min(3.0, float(layout.min_shot_sec or 1.2)))
    n = len(samples)

    # Frame-by-frame desired target choice
    # tuple: (target_face_id, speaker_id, reason, shot_mode, top_crop, bot_crop, score_map)
    desired: list[tuple[Optional[str], Optional[str], ShotReason, str, float, dict[str, float]]] = []

    for i, s in enumerate(samples):
        mouth_map = _rolling_mouth_scores(samples, i, half_win=0.50)
        spks_now = active_speakers_in_range(diar_segments, s.abs_t - 0.15, s.abs_t + 0.15)
        dom_spk = max(spks_now.items(), key=lambda kv: kv[1])[0] if spks_now else None
        spks_recent = active_speakers_in_range(diar_segments, s.abs_t - 0.85, s.abs_t + 0.85)
        hint = _active_hint_at(hints, s.abs_t)

        if eff_mode == "multi_speaker_split" and len(s.faces) >= 2:
            desired.append((s.faces[0].face_id, dom_spk, "speaker_match", "multi_speaker_split", 0.9, mouth_map))
            continue

        # Score each visible face
        f_scores: dict[str, float] = {}
        for f in s.faces:
            sc = 0.0
            sc += mouth_map.get(f.face_id, 0.0) * 0.85
            if dom_spk and spk_to_face.get(dom_spk) == f.face_id:
                sc += 4.5 * max(0.5, spk_conf.get(dom_spk, 0.85))
            if layout.pinned_speaker_id:
                pin_fid = spk_to_face.get(layout.pinned_speaker_id, layout.pinned_speaker_id)
                if f.face_id == pin_fid or face_to_spk.get(f.face_id) == layout.pinned_speaker_id:
                    sc += 12.0
            if len(s.faces) == 1:
                sc += 1.4
            sc += 0.35 * f.box.conf + 1.2 * min(0.15, f.box.w * f.box.h)
            f_scores[f.face_id] = sc

        # Reaction cutaway decision (explicit POI hint takes priority over crosstalk)
        if (
            layout.focus_strategy == "auto_topic"
            and hint is not None
            and hint.role == "reaction"
            and len(s.faces) >= 2
            and not layout.pinned_speaker_id
        ):
            main_fid = spk_to_face.get(dom_spk, "") if dom_spk else ""
            alt_faces = [f for f in s.faces if f.face_id != main_fid]
            if alt_faces:
                react_f = max(alt_faces, key=lambda f: mouth_map.get(f.face_id, 0.0) + f.box.w * f.box.h)
                desired.append((react_f.face_id, face_to_spk.get(react_f.face_id), "reaction", "single_focus", 0.76, f_scores))
                continue

        # Group mode decision for 3-5 people (explicit POI group or 3+ rapid speakers)
        if (
            layout.focus_strategy == "auto_topic"
            and layout.group_mode == "fit_all_blur"
            and len(s.faces) >= 3
            and not layout.pinned_speaker_id
            and (
                (hint is not None and hint.role == "group")
                or sum(1 for v in spks_recent.values() if v >= 0.30) >= 3
            )
        ):
            desired.append(("__GROUP__", dom_spk, "group", "blurred_frame", 0.82, f_scores))
            continue

        if f_scores:
            best_fid = max(f_scores.keys(), key=lambda fid: f_scores[fid])
            spk_lbl = dom_spk if (dom_spk and spk_to_face.get(dom_spk) == best_fid) else face_to_spk.get(best_fid, dom_spk)
            conf_val = min(1.0, max(0.35, f_scores[best_fid] / 6.0))
            reason_val: ShotReason = "manual" if layout.pinned_speaker_id else "speaker_match"
            shot_m = "blurred_frame" if eff_mode == "blurred_frame" else "single_focus"
            desired.append((best_fid, spk_lbl, reason_val, shot_m, conf_val, f_scores))
        else:
            desired.append((None, dom_spk, "fallback", "single_focus", 0.25, {}))

    # Pass B: Temporal state machine with hysteresis (>= 0.6s challenger win), min_shot_sec, and <1.0s lost-face hold
    @dataclass
    class _Run:
        t_start: float
        t_end: float
        target_fid: Optional[str]
        spk_id: Optional[str]
        reason: ShotReason
        shot_mode: str
        angle_id: int
        samples_idx: list[int]
        conf_sum: float = 0.0
        conf_cnt: int = 0

    def _challenger_holds(start_i: int, cand_fid: Optional[str]) -> bool:
        if cand_fid is None:
            return False
        t0 = samples[start_i].rel_t
        hold_ok = False
        for k in range(start_i, n):
            if samples[k].is_cut and k > start_i:
                break
            dt = samples[k].rel_t - t0
            if desired[k][0] != cand_fid:
                return dt >= 0.55
            if dt >= 0.58:
                hold_ok = True
                break
        return hold_ok or (samples[-1].rel_t - t0 >= 0.55)

    runs: list[_Run] = []
    cur_fid, cur_spk, cur_reason, cur_smode, cur_conf, _ = desired[0]
    cur_angle = samples[0].angle_id
    cur_run = _Run(
        t_start=0.0,
        t_end=samples[0].rel_t,
        target_fid=cur_fid,
        spk_id=cur_spk,
        reason=cur_reason,
        shot_mode=cur_smode,
        angle_id=cur_angle,
        samples_idx=[0],
        conf_sum=cur_conf,
        conf_cnt=1,
    )
    lost_since_t: Optional[float] = None

    for i in range(1, n):
        s = samples[i]
        cand_fid, cand_spk, cand_reason, cand_smode, cand_conf, f_scores = desired[i]
        shot_elapsed = s.rel_t - cur_run.t_start
        cur_visible = (cur_fid == "__GROUP__" and len(s.faces) >= 2) or any(f.face_id == cur_fid for f in s.faces)

        if cur_visible or cur_fid is None:
            lost_since_t = None
        elif lost_since_t is None:
            lost_since_t = s.rel_t

        switch = False
        # 1) Camera angle cut handling
        if s.is_cut:
            # If cut moved us to a camera angle showing same person -> cut boundary (new crop coords) or switch to visible person
            if not cur_visible or (cand_fid != cur_fid and shot_elapsed >= 0.55):
                switch = True
            elif cand_fid == cur_fid:
                # Same speaker across camera angle cut: open a clean new segment at new frame position!
                switch = True
        # 2) Long occlusion fallback (>= 1.0s lost face)
        elif not cur_visible and lost_since_t is not None and (s.rel_t - lost_since_t) >= 1.0 and cand_fid is not None:
            switch = True
        # 3) Normal conversational switch inside same shot
        elif cand_fid != cur_fid and cand_fid is not None:
            min_need = min_shot if cand_reason != "reaction" else max(0.85, min_shot * 0.75)
            cur_sc = f_scores.get(cur_fid or "", 0.0)
            cand_sc = f_scores.get(cand_fid, 0.0)
            strong_ratio = (cand_reason in ("reaction", "group")) or (cand_sc >= max(1.0, cur_sc * 1.25))
            if (shot_elapsed >= min_need or (cand_reason == "reaction" and shot_elapsed >= 0.25)) and (
                not cur_visible or (strong_ratio and _challenger_holds(i, cand_fid))
            ):
                switch = True

        if switch:
            cut_t = round(s.rel_t, 3)
            cur_run.t_end = cut_t
            runs.append(cur_run)
            cur_fid, cur_spk, cur_reason, cur_smode, cur_angle = (
                cand_fid,
                cand_spk,
                cand_reason,
                cand_smode,
                s.angle_id,
            )
            cur_run = _Run(
                t_start=cut_t,
                t_end=cut_t,
                target_fid=cur_fid,
                spk_id=cur_spk,
                reason=cur_reason,
                shot_mode=cur_smode,
                angle_id=cur_angle,
                samples_idx=[i],
                conf_sum=cand_conf,
                conf_cnt=1,
            )
            lost_since_t = None
        else:
            cur_run.t_end = s.rel_t
            cur_run.samples_idx.append(i)
            cur_run.conf_sum += cand_conf
            cur_run.conf_cnt += 1

    cur_run.t_end = round(duration, 3)
    runs.append(cur_run)

    # Merge ultra-short runs (< 0.8s unless forced at cut, keep at least 2 angles if clip >= 2s)
    merged: list[_Run] = []
    for r in runs:
        dur_r = r.t_end - r.t_start
        if merged and dur_r < 0.75:
            prev_r = merged[-1]
            # Preserve distinct camera cuts if no identical angle overlap
            if samples[r.samples_idx[0]].is_cut and dur_r >= 0.45 and len(runs) <= 12:
                merged.append(r)
            else:
                prev_r.t_end = r.t_end
                prev_r.samples_idx.extend(r.samples_idx)
                prev_r.conf_sum += r.conf_sum
                prev_r.conf_cnt += r.conf_cnt
        else:
            merged.append(r)

    if len(merged) >= 2 and (merged[0].t_end - merged[0].t_start) < 0.70:
        merged[1].t_start = 0.0
        merged[1].samples_idx = merged[0].samples_idx + merged[1].samples_idx
        merged.pop(0)

    while len(merged) > 28:
        shortest_i = min(range(len(merged)), key=lambda idx: merged[idx].t_end - merged[idx].t_start)
        if shortest_i > 0:
            merged[shortest_i - 1].t_end = merged[shortest_i].t_end
            merged[shortest_i - 1].samples_idx.extend(merged[shortest_i].samples_idx)
            merged.pop(shortest_i)
        else:
            merged[1].t_start = 0.0
            merged.pop(0)

    # Materialize ShotCropSegments with smoothed EMA center per shot
    segments: list[ShotCropSegment] = []
    last_good_top: CropBoxConfig = CropBoxConfig(auto_detect=True, x=0.33, y=0.0, w=0.34, h=1.0, label="Subjek")
    last_good_bot: CropBoxConfig = CropBoxConfig(auto_detect=True, x=0.20, y=0.0, w=0.60, h=1.0, label="Panel Bawah")

    for r_idx, r in enumerate(merged, start=1):
        run_samples = [samples[k] for k in r.samples_idx]
        lbl_base = layout.speaker_aliases.get(r.spk_id or "", r.spk_id or f"Speaker #{r_idx}")
        conf_avg = round(r.conf_sum / max(1, r.conf_cnt), 3)

        if r.shot_mode == "multi_speaker_split":
            pairs = [
                sorted(s.faces, key=lambda f: f.box.cx)[:2]
                for s in run_samples
                if len(s.faces) >= 2
            ]
            if pairs:
                lcx = float(np.median([p[0].box.cx for p in pairs]))
                lcy = float(np.median([p[0].box.cy for p in pairs]))
                rcx = float(np.median([p[1].box.cx for p in pairs]))
                rcy = float(np.median([p[1].box.cy for p in pairs]))
                top_crop = _make_single_crop(lcx, lcy, w=0.36, h=0.65, label=f"{lbl_base} (Kiri)")
                bot_crop = _make_single_crop(rcx, rcy, w=0.36, h=0.65, label="Lawan Bicara (Kanan)")
                last_good_top, last_good_bot = top_crop, bot_crop
            else:
                top_crop, bot_crop = last_good_top.model_copy(deep=True), last_good_bot.model_copy(deep=True)
            segments.append(
                ShotCropSegment(
                    t_start=round(r.t_start, 3),
                    t_end=round(r.t_end, 3),
                    top_crop=top_crop,
                    bottom_crop=bot_crop,
                    shot_mode="multi_speaker_split",
                    speaker_id=r.spk_id,
                    face_id=r.target_fid,
                    reason=r.reason,
                    confidence=conf_avg,
                )
            )
            continue

        if r.shot_mode == "blurred_frame" and r.target_fid == "__GROUP__":
            all_faces = [f for s in run_samples for f in s.faces]
            top_crop = _make_group_crop(all_faces, label="Semua Pembicara")
            bot_crop = CropBoxConfig(auto_detect=True, x=0.22, y=0.0, w=0.56, h=1.0, label="Latar Blur")
            last_good_top, last_good_bot = top_crop, bot_crop
            segments.append(
                ShotCropSegment(
                    t_start=round(r.t_start, 3),
                    t_end=round(r.t_end, 3),
                    top_crop=top_crop,
                    bottom_crop=bot_crop,
                    shot_mode="blurred_frame",
                    speaker_id=r.spk_id,
                    face_id=None,
                    reason="group",
                    confidence=conf_avg,
                )
            )
            continue

        matching_boxes = [
            f.box
            for s in run_samples
            for f in s.faces
            if r.target_fid is None or f.face_id == r.target_fid
        ]
        if not matching_boxes:
            matching_boxes = [f.box for s in run_samples for f in s.faces]

        if matching_boxes:
            # EMA / trimmed-median smoothing for gentle head motion stability
            cxs = [b.cx for b in matching_boxes]
            cys = [b.cy for b in matching_boxes]
            med_cx = float(np.median(cxs))
            med_cy = float(np.median(cys))
            w_crop = 0.72 if eff_mode == "blurred_frame" else 0.34
            h_crop = 0.96 if eff_mode == "blurred_frame" else 1.0
            top_crop = _make_single_crop(med_cx, med_cy, w=w_crop, h=h_crop, label=lbl_base)
            bot_crop = CropBoxConfig(auto_detect=True, x=0.22, y=0.0, w=0.56, h=1.0, label="Panel Bawah")
            last_good_top, last_good_bot = top_crop, bot_crop
            reason_final = r.reason
        else:
            # No visible face at all in shot: hold last good crop or safe center fallback
            top_crop = last_good_top.model_copy(deep=True)
            bot_crop = last_good_bot.model_copy(deep=True)
            reason_final = "fallback"

        segments.append(
            ShotCropSegment(
                t_start=round(r.t_start, 3),
                t_end=round(r.t_end, 3),
                top_crop=top_crop,
                bottom_crop=bot_crop if eff_mode == "multi_speaker_split" else None,
                shot_mode="blurred_frame" if r.shot_mode == "blurred_frame" else "single_focus",
                speaker_id=r.spk_id,
                face_id=None if r.target_fid == "__GROUP__" else r.target_fid,
                reason=reason_final,
                confidence=conf_avg,
            )
        )

    if segments:
        segments[0].t_start = 0.0
        segments[-1].t_end = round(duration, 3)

    return segments


def plan_podcast_shots_sync(
    source_path: Path,
    start_sec: float,
    end_sec: float,
    layout: LayoutLayerConfig,
    src_w: int = 1920,
    src_h: int = 1080,
    fps: float = 30.0,
    job_dir: Optional[Path] = None,
    diarization_segments: Optional[list[SpeakerSegment]] = None,
    focus_hints: Optional[list[FocusHintItem]] = None,
    transcript_segments: Optional[list[TranscriptSegment]] = None,
    mode_override: Optional[ComposerLayoutMode] = None,
) -> list[ShotCropSegment]:
    """Full V2 speaker-aware + POI podcast detection pipeline returning `ShotCropSegment` list."""
    duration = max(0.5, round(float(end_sec) - float(start_sec), 3))
    samples = _sample_video_observations(
        source_path=source_path,
        start_sec=max(0.0, float(start_sec)),
        end_sec=max(float(start_sec) + 0.5, float(end_sec)),
        fps_hint=fps,
    )
    if not samples:
        return []

    diar = diarization_segments
    if diar is None:
        diar = diarize_audio_window_sync(
            source_path=source_path,
            start_sec=max(0.0, float(start_sec)),
            end_sec=max(float(start_sec) + 0.5, float(end_sec)),
            job_dir=job_dir,
        )

    hints = list(focus_hints or [])
    if not hints and transcript_segments:
        from atro.modules.analyzer.validator import build_heuristic_focus_hints

        hints = build_heuristic_focus_hints(transcript_segments, start_sec, end_sec)

    planned = plan_podcast_shots_from_observations(
        samples=samples,
        duration=duration,
        layout=layout,
        diar_segments=diar,
        focus_hints=hints,
        mode=mode_override,
    )

    if job_dir is not None and planned:
        spk_to_face, spk_conf = correlate_speakers_and_faces(samples, diar)
        if spk_to_face:
            smap = load_job_speaker_map(job_dir)
            smap.bindings = [
                SpeakerFaceBinding(
                    speaker_id=spk,
                    face_id=fid,
                    confidence=spk_conf.get(spk, 0.75),
                )
                for spk, fid in spk_to_face.items()
            ]
            save_job_speaker_map(job_dir, smap)

    return planned
