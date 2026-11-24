"""Automatic cover renderer and one-click Publish Kit ZIP exporter for YouTube Shorts & TikTok."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from atro.core.presets import generate_social_copywriting
from atro.core.schemas import PlatformPreset, RenderedClip, TranscriptSegment


def _fit_center_crop_portrait(frame_bgr: np.ndarray, width: int = 1080, height: int = 1920) -> np.ndarray:
    """Resize and center-crop any BGR frame cleanly to 1080x1920 portrait."""
    if frame_bgr is None or frame_bgr.size == 0:
        canvas = np.zeros((height, width, 3), dtype=np.uint8)
        canvas[:, :] = (13, 14, 15)
        return canvas
    fh, fw = frame_bgr.shape[:2]
    target_ar = width / float(height)
    src_ar = fw / float(max(1, fh))
    if src_ar > target_ar:
        new_w = max(1, int(round(fh * target_ar)))
        x0 = max(0, (fw - new_w) // 2)
        cropped = frame_bgr[:, x0 : x0 + new_w]
    else:
        new_h = max(1, int(round(fw / target_ar)))
        y0 = max(0, (fh - new_h) // 2)
        cropped = frame_bgr[y0 : y0 + new_h, :]
    return cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LANCZOS4)


def _extract_sharp_cover_frame(
    video_path: Path,
    timestamp_sec: Optional[float] = None,
) -> Optional[np.ndarray]:
    """Pick the sharpest frame around key visual candidates via Laplacian variance."""
    if not video_path.is_file():
        return None
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = total_frames / max(1.0, fps) if total_frames > 0 else 5.0
        probe_times = (
            [max(0.0, min(duration - 0.1, float(timestamp_sec)))]
            if timestamp_sec is not None
            else [max(0.1, duration * r) for r in (0.18, 0.38, 0.58)]
        )

        best_frame: Optional[np.ndarray] = None
        best_score = -1.0
        for t in probe_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            if sharpness > best_score:
                best_score = sharpness
                best_frame = frame
        return best_frame
    finally:
        cap.release()


def _fallback_studio_gradient(width: int = 1080, height: int = 1920) -> np.ndarray:
    """Create a warm charcoal gradient background when video capture fails."""
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    # BGR warm obsidian top -> subtle amber ember glow middle -> dark bottom
    glow = np.exp(-((y - 0.45) ** 2) / 0.08)
    canvas[:, :, 0] = np.clip(13 + 12 * glow, 0, 255).astype(np.uint8)
    canvas[:, :, 1] = np.clip(14 + 28 * glow, 0, 255).astype(np.uint8)
    canvas[:, :, 2] = np.clip(16 + 74 * glow, 0, 255).astype(np.uint8)
    return canvas


def _apply_cover_scrim(portrait_bgr: np.ndarray) -> np.ndarray:
    """Blend subtle top/bottom dark vignette gradients so title typography always pops."""
    h, w = portrait_bgr.shape[:2]
    out = portrait_bgr.astype(np.float32)
    y_norm = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    top_fade = np.clip((0.36 - y_norm) / 0.36, 0.0, 1.0) * 0.65
    bot_fade = np.clip((y_norm - 0.68) / 0.32, 0.0, 1.0) * 0.60
    darken = np.clip(1.0 - (top_fade + bot_fade), 0.25, 1.0)
    out *= darken
    return np.clip(out, 0, 255).astype(np.uint8)


def _draw_cover_text_qt(
    portrait_bgr: np.ndarray,
    hook_title: str,
    subtitle_badge: str,
    platform_label: str,
    output_png_path: Path,
) -> bool:
    """Render high-DPI QPainter cover typography when Qt GUI is active on the main thread."""
    try:
        from PyQt6.QtCore import Qt, QRectF
        from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath

        if (
            QGuiApplication.instance() is None
            or threading.current_thread() is not threading.main_thread()
        ):
            return False

        rgb = cv2.cvtColor(portrait_bgr, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()

        painter = QPainter(qimg)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Platform pill badge top-left
        pill_rect = QRectF(72, 110, 310, 56)
        pill_path = QPainterPath()
        pill_path.addRoundedRect(pill_rect, 28, 28)
        painter.fillPath(pill_path, QColor("#F97316"))
        painter.setPen(QColor("#FFFFFF"))
        badge_font = QFont("Segoe UI", 22, QFont.Weight.Bold)
        painter.setFont(badge_font)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, platform_label)

        if subtitle_badge.strip():
            sub_rect = QRectF(400, 110, 608, 56)
            sub_path = QPainterPath()
            sub_path.addRoundedRect(sub_rect, 28, 28)
            painter.fillPath(sub_path, QColor(15, 14, 13, 185))
            painter.setPen(QColor("#E2DDD5"))
            sub_font = QFont("Segoe UI", 20, QFont.Weight.DemiBold)
            painter.setFont(sub_font)
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, subtitle_badge.strip()[:36])

        # Hook Title Backdrop Card near top-upper third (safe 9:16 top zone)
        title_box = QRectF(72, 200, w - 144, 340)
        box_path = QPainterPath()
        box_path.addRoundedRect(title_box, 26, 26)
        painter.fillPath(box_path, QColor(15, 14, 13, 195))
        painter.setPen(QColor(249, 115, 22, 160))
        painter.drawPath(box_path)

        title_font = QFont("Segoe UI", 48, QFont.Weight.Black)
        painter.setFont(title_font)
        inner_rect = QRectF(108, 224, w - 216, 292)
        painter.setPen(QColor("#FAF8F5"))
        painter.drawText(
            inner_rect,
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            hook_title.strip() or "Klip Podcast Terbaik",
        )
        painter.end()

        output_png_path.parent.mkdir(parents=True, exist_ok=True)
        return bool(qimg.save(str(output_png_path), "PNG"))
    except Exception:
        return False


def _draw_cover_text_cv2(
    portrait_bgr: np.ndarray,
    hook_title: str,
    subtitle_badge: str,
    platform_label: str,
    output_png_path: Path,
) -> Path:
    """Thread-safe OpenCV fallback cover renderer when no Qt main-thread GUI is active."""
    canvas = portrait_bgr.copy()
    h, w = canvas.shape[:2]

    # Top orange badge pill
    cv2.rectangle(canvas, (72, 110), (390, 170), (22, 115, 249), -1)
    cv2.putText(
        canvas,
        platform_label,
        (96, 150),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.95,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    if subtitle_badge.strip():
        cv2.rectangle(canvas, (410, 110), (w - 72, 170), (15, 14, 13), -1)
        cv2.putText(
            canvas,
            subtitle_badge.strip()[:30],
            (432, 150),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.85,
            (226, 221, 213),
            2,
            cv2.LINE_AA,
        )

    # Dark semi-transparent title plate
    overlay = canvas.copy()
    cv2.rectangle(overlay, (72, 200), (w - 72, 520), (15, 14, 13), -1)
    cv2.addWeighted(overlay, 0.78, canvas, 0.22, 0, canvas)
    cv2.rectangle(canvas, (72, 200), (w - 72, 520), (22, 115, 249), 3)

    words = (hook_title.strip() or "Klip Terbaik").split()
    lines: list[str] = []
    cur = ""
    for wd in words:
        cand = f"{cur} {wd}".strip()
        if len(cand) > 22 and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = cand
    if cur:
        lines.append(cur)
    lines = lines[:4]

    start_y = 285
    for idx, line in enumerate(lines):
        y_pos = start_y + idx * 62
        cv2.putText(
            canvas,
            line,
            (108, y_pos),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.55,
            (250, 248, 245),
            4,
            cv2.LINE_AA,
        )

    output_png_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_png_path), canvas)
    return output_png_path


def render_clip_cover_image(
    video_path: Path,
    output_png_path: Path,
    hook_title: str,
    subtitle_badge: str = "",
    timestamp_sec: Optional[float] = None,
    platform_target: PlatformPreset = "youtube_shorts",
) -> Path:
    """Extract sharp video keyframe and render a 1080x1920 Shorts/TikTok cover PNG."""
    raw = _extract_sharp_cover_frame(video_path, timestamp_sec=timestamp_sec)
    portrait = (
        _fit_center_crop_portrait(raw, 1080, 1920)
        if raw is not None
        else _fallback_studio_gradient(1080, 1920)
    )
    scrim = _apply_cover_scrim(portrait)
    plat_label = "TIKTOK" if platform_target == "tiktok" else "YOUTUBE SHORTS"

    if _draw_cover_text_qt(scrim, hook_title, subtitle_badge, plat_label, output_png_path):
        return output_png_path
    return _draw_cover_text_cv2(scrim, hook_title, subtitle_badge, plat_label, output_png_path)


def build_publish_metadata_text(
    clip: RenderedClip,
    platform_target: PlatformPreset = "youtube_shorts",
    preset_id: str = "podcast",
) -> str:
    """Format ready-to-paste copywriting, hashtags, and technical metadata for Shorts/TikTok."""
    copy_data = generate_social_copywriting(
        clip_title=clip.title,
        reason=clip.reason,
        preset_id=preset_id,
        platform_target=platform_target,
    )
    plat_name = "TikTok" if platform_target == "tiktok" else "YouTube Shorts"
    return (
        f"PLATFORM TARGET : {plat_name}\n"
        f"JUDUL HOOK      : {clip.title}\n"
        f"SKOR VIRAL      : {clip.virality_score}/10\n"
        f"DURASI          : {clip.output_duration_sec:.1f} detik (9:16 • -14 LUFS)\n"
        f"------------------------------------------------------------\n\n"
        f"[HEADLINE]\n{copy_data['hook_headline']}\n\n"
        f"[CAPTION SIAP UPLOAD]\n{copy_data['caption']}\n\n"
        f"[HASHTAGS]\n{copy_data['hashtags_text']}\n"
    )


def _safe_clip_slug(title: str, index: int) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", (title or "").replace("\r", " ").replace("\n", " "))
    cleaned = " ".join(cleaned.split()).strip(" ._-")
    if not cleaned:
        return f"Klip {index}"
    return cleaned[:90].strip(" ._-") or f"Klip {index}"


def export_publish_kit_zip(
    clip: RenderedClip,
    output_zip_path: Path,
    platform_target: PlatformPreset = "youtube_shorts",
    preset_id: str = "podcast",
    transcript_segments: Optional[list[TranscriptSegment]] = None,
    cover_image_path: Optional[Path] = None,
) -> Path:
    """Bundle MP4, 1080x1920 cover.png, metadata.txt, and caption.json into one ZIP archive."""
    output_zip_path = Path(output_zip_path)
    output_zip_path.parent.mkdir(parents=True, exist_ok=True)
    slug = _safe_clip_slug(clip.title, clip.clip_index)

    copy_data = generate_social_copywriting(
        clip_title=clip.title,
        reason=clip.reason,
        preset_id=preset_id,
        platform_target=platform_target,
    )
    meta_txt = build_publish_metadata_text(
        clip,
        platform_target=platform_target,
        preset_id=preset_id,
    )

    clip_segs: list[dict[str, Any]] = []
    for seg in transcript_segments or []:
        if seg.end <= clip.padded_start_sec or seg.start >= clip.padded_end_sec:
            continue
        clip_segs.append(
            {
                "start": round(max(0.0, seg.start - clip.padded_start_sec), 3),
                "end": round(max(0.0, seg.end - clip.padded_start_sec), 3),
                "text": seg.text,
            }
        )

    caption_payload = {
        "clip_index": clip.clip_index,
        "title": clip.title,
        "platform_target": platform_target,
        "virality_score": clip.virality_score,
        "duration_sec": clip.output_duration_sec,
        "hook_headline": copy_data["hook_headline"],
        "caption": copy_data["caption"],
        "hashtags": copy_data["hashtags"],
        "segments": clip_segs,
    }

    video_src = Path(clip.output_path)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        cover_path = cover_image_path if (cover_image_path and cover_image_path.is_file()) else None
        if cover_path is None:
            cover_path = render_clip_cover_image(
                video_path=video_src,
                output_png_path=tmp_path / "cover.png",
                hook_title=clip.title,
                subtitle_badge=f"Viral {clip.virality_score}/10 • {clip.output_duration_sec:.0f}s",
                platform_target=platform_target,
            )

        with zipfile.ZipFile(output_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if video_src.is_file():
                zf.write(video_src, arcname=f"{slug}.mp4")
            zf.write(cover_path, arcname="cover.png")
            zf.writestr("metadata.txt", meta_txt.encode("utf-8"))
            zf.writestr(
                "caption.json",
                json.dumps(caption_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            )

    return output_zip_path
