"""Cross-angle face identity clustering via OpenCV SFace ONNX + torso/skin appearance."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from atro.modules.cutter.framing import face_tracking as ft_mod
from atro.modules.cutter.framing.face_tracking import FaceBox

_ORIG_DETECT_FN = ft_mod._detect_faces_in_frame
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _resolve_sface_model_path() -> Optional[Path]:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "atro" / "assets" / "face_recognition_sface_2021dec.onnx")
    candidates.append(Path(__file__).resolve().parents[3] / "assets" / "face_recognition_sface_2021dec.onnx")
    for p in candidates:
        if p.is_file() and p.stat().st_size > 1_000_000:
            return p
    return None


def load_sface_recognizer() -> Any:
    """Load OpenCV FaceRecognizerSF ONNX model when available, returning None on fallback."""
    model_path = _resolve_sface_model_path()
    if model_path is None or not hasattr(cv2, "FaceRecognizerSF"):
        return None
    try:
        return cv2.FaceRecognizerSF.create(str(model_path), "")
    except Exception:
        return None


def _l2_normalize(vec: Optional[np.ndarray]) -> Optional[np.ndarray]:
    if vec is None or vec.size == 0:
        return None
    flat = vec.reshape(-1).astype(np.float32)
    norm = float(np.linalg.norm(flat))
    if norm <= 1e-8:
        return None
    return flat / norm


def extract_appearance_embedding(frame_bgr: np.ndarray, box: FaceBox) -> Optional[np.ndarray]:
    """Compute a normalized HSV appearance signature across face + upper-torso clothing."""
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    sh, sw = frame_bgr.shape[:2]
    x1 = max(0, min(sw - 2, int(round(box.x * sw))))
    x2 = max(x1 + 2, min(sw, int(round(box.x2 * sw))))
    y1 = max(0, min(sh - 2, int(round(box.y * sh))))
    y2 = max(y1 + 2, min(sh, int(round(box.y2 * sh))))

    # Face region histogram (hue+saturation)
    face_roi = frame_bgr[y1:y2, x1:x2]
    if face_roi.size == 0:
        return None
    face_hsv = cv2.cvtColor(face_roi, cv2.COLOR_BGR2HSV)
    h_face = cv2.calcHist([face_hsv], [0, 1], None, [12, 4], [0, 180, 0, 256]).reshape(-1)

    # Upper-torso clothing below face box (high cross-angle discriminability in seated podcasts)
    tx1 = max(0, min(sw - 2, int(round((box.x - box.w * 0.25) * sw))))
    tx2 = max(tx1 + 2, min(sw, int(round((box.x2 + box.w * 0.25) * sw))))
    ty1 = max(0, min(sh - 2, y2))
    ty2 = max(ty1 + 2, min(sh, int(round((box.y2 + box.h * 1.35) * sh))))
    if ty2 > ty1 + 4 and tx2 > tx1 + 4:
        torso_roi = frame_bgr[ty1:ty2, tx1:tx2]
        torso_hsv = cv2.cvtColor(torso_roi, cv2.COLOR_BGR2HSV)
        h_torso = cv2.calcHist([torso_hsv], [0, 1], None, [16, 4], [0, 180, 0, 256]).reshape(-1)
    else:
        h_torso = np.zeros(64, dtype=np.float32)

    combined = np.concatenate([h_face.astype(np.float32) * 0.35, h_torso.astype(np.float32) * 0.65])
    return _l2_normalize(combined)


def extract_sface_embedding(
    frame_bgr: np.ndarray,
    yunet_row: Optional[np.ndarray],
    sface: Any,
) -> Optional[np.ndarray]:
    """Extract normalized 128-D SFace embedding using YuNet 5-point landmarks."""
    if sface is None or yunet_row is None or frame_bgr is None or frame_bgr.size == 0:
        return None
    try:
        if len(yunet_row) < 14:
            return None
        aligned = sface.alignCrop(frame_bgr, yunet_row)
        feat = sface.feature(aligned)
        return _l2_normalize(feat)
    except Exception:
        return None


@dataclass(slots=True)
class DetectedFaceObs:
    """Single face observation enriched with embeddings and visual speech activity."""

    box: FaceBox
    sface_vec: Optional[np.ndarray] = None
    app_vec: Optional[np.ndarray] = None
    face_id: str = "FACE_A"


def detect_faces_with_features(
    small_bgr: np.ndarray,
    gray: np.ndarray,
    yunet: Any,
    sface: Any,
    frontal_clf: Any,
    profile_clf: Any,
    run_haar_fallback: bool = True,
) -> list[DetectedFaceObs]:
    """Detect faces on one downscaled frame and attach SFace + appearance descriptors."""
    sh, sw = small_bgr.shape[:2]
    if ft_mod._detect_faces_in_frame is _ORIG_DETECT_FN and yunet is not None:
        yunet.setInputSize((sw, sh))
        _, faces_arr = yunet.detect(small_bgr)
        obs: list[DetectedFaceObs] = []
        if faces_arr is not None:
            for row in faces_arr:
                bx, by, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
                conf = float(row[-1]) if len(row) > 4 else 0.9
                if bw / sw >= 0.032 and bh / sh >= 0.032:
                    fb = FaceBox(
                        x=max(0.0, bx / sw),
                        y=max(0.0, by / sh),
                        w=min(1.0, bw / sw),
                        h=min(1.0, bh / sh),
                        conf=conf,
                    )
                    sf_vec = extract_sface_embedding(small_bgr, row, sface)
                    ap_vec = extract_appearance_embedding(small_bgr, fb)
                    obs.append(DetectedFaceObs(box=fb, sface_vec=sf_vec, app_vec=ap_vec))
        if obs:
            return obs
        if not run_haar_fallback:
            return []

    raw_boxes = ft_mod._detect_faces_in_frame(
        small_bgr,
        gray,
        yunet,
        frontal_clf,
        profile_clf,
        run_haar_fallback=run_haar_fallback,
    )
    return [
        DetectedFaceObs(
            box=b,
            sface_vec=None,
            app_vec=extract_appearance_embedding(small_bgr, b),
        )
        for b in raw_boxes
    ]


@dataclass
class _IdentityCluster:
    face_id: str
    sface_sum: Optional[np.ndarray] = None
    sface_count: int = 0
    app_sum: Optional[np.ndarray] = None
    app_count: int = 0
    last_cx: float = 0.5
    last_cy: float = 0.4
    last_angle_id: int = -1
    hit_count: int = 0

    def sface_proto(self) -> Optional[np.ndarray]:
        return _l2_normalize(self.sface_sum)

    def app_proto(self) -> Optional[np.ndarray]:
        return _l2_normalize(self.app_sum)

    def update(self, obs: DetectedFaceObs, angle_id: int) -> None:
        self.hit_count += 1
        self.last_cx = obs.box.cx
        self.last_cy = obs.box.cy
        self.last_angle_id = angle_id
        if obs.sface_vec is not None:
            if self.sface_sum is None:
                self.sface_sum = obs.sface_vec.copy()
            else:
                self.sface_sum += obs.sface_vec
            self.sface_count += 1
        if obs.app_vec is not None:
            if self.app_sum is None:
                self.app_sum = obs.app_vec.copy()
            else:
                self.app_sum += obs.app_vec
            self.app_count += 1


class FaceIdentityTracker:
    """Assign persistent cross-camera `FACE_A..FACE_Z` identities respecting same-frame uniqueness."""

    def __init__(self, sface_threshold: float = 0.30, app_threshold: float = 0.72) -> None:
        self._sface_thresh = sface_threshold
        self._app_thresh = app_threshold
        self._clusters: list[_IdentityCluster] = []

    def _next_face_id(self) -> str:
        idx = len(self._clusters)
        if idx < len(_LETTERS):
            return f"FACE_{_LETTERS[idx]}"
        return f"FACE_{idx + 1}"

    @property
    def known_face_ids(self) -> list[str]:
        return [c.face_id for c in self._clusters]

    def assign_frame(
        self,
        observations: list[DetectedFaceObs],
        angle_id: int,
        is_cut: bool = False,
    ) -> list[DetectedFaceObs]:
        """Assign unique `face_id` per face in one frame, matching across camera angles."""
        if not observations:
            return []

        # Track largest/highest-confidence faces first to anchor identity assignments cleanly
        ordered_indices = sorted(
            range(len(observations)),
            key=lambda i: (observations[i].box.conf, observations[i].box.w * observations[i].box.h),
            reverse=True,
        )
        used_cluster_ids: set[str] = set()

        for obs_idx in ordered_indices:
            obs = observations[obs_idx]
            best_cl: Optional[_IdentityCluster] = None
            best_score = -1.0

            for cl in self._clusters:
                if cl.face_id in used_cluster_ids:
                    continue

                sf_sim: Optional[float] = None
                sf_proto = cl.sface_proto()
                if obs.sface_vec is not None and sf_proto is not None:
                    sf_sim = float(np.dot(obs.sface_vec, sf_proto))

                ap_sim: Optional[float] = None
                ap_proto = cl.app_proto()
                if obs.app_vec is not None and ap_proto is not None:
                    ap_sim = float(np.dot(obs.app_vec, ap_proto))

                same_shot = (not is_cut) and (cl.last_angle_id == angle_id)
                dist_xy = ((obs.box.cx - cl.last_cx) ** 2 + (obs.box.cy - cl.last_cy) ** 2) ** 0.5

                matched = False
                score = -1.0
                if same_shot and dist_xy <= 0.14:
                    # Continuous camera shot: spatial proximity + mild appearance check keeps continuity
                    if sf_sim is None or sf_sim >= 0.20:
                        matched = True
                        score = 1.0 - dist_xy + (0.4 * max(0.0, sf_sim or 0.3))
                elif sf_sim is not None and sf_sim >= self._sface_thresh:
                    # Cross-angle identity match via 128-D SFace embedding
                    matched = True
                    score = sf_sim + (0.15 * max(0.0, ap_sim or 0.0))
                elif sf_sim is None and ap_sim is not None and ap_sim >= self._app_thresh:
                    # Profile / fallback without landmarks: match via face+clothing histogram
                    matched = True
                    score = ap_sim * 0.90

                if matched and score > best_score:
                    best_score = score
                    best_cl = cl

            if best_cl is None:
                best_cl = _IdentityCluster(face_id=self._next_face_id())
                self._clusters.append(best_cl)

            best_cl.update(obs, angle_id=angle_id)
            obs.face_id = best_cl.face_id
            used_cluster_ids.add(best_cl.face_id)

        return observations
