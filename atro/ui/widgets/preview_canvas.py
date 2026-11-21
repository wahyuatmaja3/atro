"""Real-time 9:16 WYSIWYG Preview Canvas and Interactive Source Crop Selector widgets."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import QSizePolicy, QWidget

from atro.core.composer_schema import ClipComposerConfig, CropBoxConfig
from atro.core.schemas import TranscriptSegment
from atro.modules.cutter.composer_renderer import render_preview_frame


class FrameExtractorCache:
    """Fast LRU-cached OpenCV frame reader for real-time scrubbing and 30 FPS preview."""

    def __init__(self, max_cached: int = 48) -> None:
        self._max_cached = max(8, max_cached)
        self._cache: OrderedDict[tuple[str, float], np.ndarray] = OrderedDict()
        self._cap: Optional[cv2.VideoCapture] = None
        self._cap_path: Optional[str] = None
        self._last_pos_sec: float = -999.0

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._cap_path = None
        self._last_pos_sec = -999.0

    def _ensure_cap(self, source_path: Path) -> Optional[cv2.VideoCapture]:
        resolved = str(source_path.resolve())
        if self._cap is not None and self._cap_path == resolved and self._cap.isOpened():
            return self._cap
        self.close()
        if not source_path.is_file():
            return None
        cap = cv2.VideoCapture(resolved)
        if not cap.isOpened():
            return None
        self._cap = cap
        self._cap_path = resolved
        self._last_pos_sec = -999.0
        return cap

    def get_video_info(self, source_path: Path) -> tuple[int, int, float, float]:
        """Return `(width, height, fps, duration_sec)` for a local video file."""
        cap = self._ensure_cap(source_path)
        if cap is None:
            return 1920, 1080, 30.0, 30.0
        w = max(2, int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920))
        h = max(2, int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        if fps <= 1.0 or fps > 240.0:
            fps = 30.0
        frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        dur = max(1.0, frames / fps) if frames > 0 else 30.0
        return w, h, fps, dur

    def get_frame(self, source_path: Optional[Path], timestamp_sec: float) -> Optional[np.ndarray]:
        """Extract BGR frame near `timestamp_sec`, downscaled cleanly for fast UI compositing."""
        if source_path is None or not source_path.is_file():
            return None
        resolved = str(source_path.resolve())
        key_t = round(max(0.0, float(timestamp_sec)), 2)
        cache_key = (resolved, key_t)
        if cache_key in self._cache:
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        cap = self._ensure_cap(source_path)
        if cap is None:
            return None

        delta = key_t - self._last_pos_sec
        if 0.0 <= delta <= 0.055 and self._last_pos_sec >= 0.0:
            ok, frame = cap.read()
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, key_t * 1000.0)
            ok, frame = cap.read()

        if not ok or frame is None:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, (key_t - 0.1) * 1000.0))
            ok, frame = cap.read()
        if not ok or frame is None:
            return None

        self._last_pos_sec = key_t
        fh, fw = frame.shape[:2]
        if fw > 1280:
            scale = 1280.0 / fw
            frame = cv2.resize(
                frame,
                (1280, max(2, int(round(fh * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        self._cache[cache_key] = frame
        while len(self._cache) > self._max_cached:
            self._cache.popitem(last=False)
        return frame


class SourceCropSelectorWidget(QWidget):
    """Interactive 16:9 source viewer allowing drag/resize of Top (Subject) & Bottom (Gameplay) crop boxes."""

    crop_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self._config: Optional[ClipComposerConfig] = None
        self._frame_qimg: Optional[QImage] = None
        self._frame_rect = QRectF()
        self._rel_time_sec: float = 0.0

        # Drag state: (box_name, mode, start_pos, orig_rect)
        self._drag_target: Optional[str] = None  # "top" or "bottom"
        self._drag_mode: Optional[str] = None  # "move", "nw", "ne", "sw", "se"
        self._drag_start_pt = QPointF()
        self._drag_orig_box: tuple[float, float, float, float] = (0.0, 0.0, 0.5, 0.5)

    def set_state(
        self,
        config: Optional[ClipComposerConfig],
        frame_bgr: Optional[np.ndarray],
        rel_time_sec: float = 0.0,
    ) -> None:
        self._config = config
        self._rel_time_sec = max(0.0, float(rel_time_sec))
        if frame_bgr is not None and frame_bgr.size > 0:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            self._frame_qimg = QImage(
                rgb.data, w, h, ch * w, QImage.Format.Format_RGB888
            ).copy()
        else:
            self._frame_qimg = None
        self.update()

    def _compute_display_rect(self) -> QRectF:
        avail = QRectF(8.0, 8.0, max(40.0, self.width() - 16.0), max(40.0, self.height() - 16.0))
        if self._frame_qimg is not None and not self._frame_qimg.isNull():
            aspect = self._frame_qimg.width() / max(1, self._frame_qimg.height())
        else:
            aspect = 16.0 / 9.0

        if avail.width() / avail.height() > aspect:
            dh = avail.height()
            dw = dh * aspect
        else:
            dw = avail.width()
            dh = dw / aspect
        dx = avail.x() + (avail.width() - dw) * 0.5
        dy = avail.y() + (avail.height() - dh) * 0.5
        return QRectF(dx, dy, dw, dh)

    def _box_to_widget_rect(self, crop: CropBoxConfig, frame_rect: QRectF) -> QRectF:
        return QRectF(
            frame_rect.x() + crop.x * frame_rect.width(),
            frame_rect.y() + crop.y * frame_rect.height(),
            crop.w * frame_rect.width(),
            crop.h * frame_rect.height(),
        )

    def _active_boxes(self) -> list[tuple[str, CropBoxConfig, QColor, str]]:
        if self._config is None:
            return []
        layout = self._config.layout
        top_crop, bot_crop = layout.resolve_crops_at_time(self._rel_time_sec)
        boxes: list[tuple[str, CropBoxConfig, QColor, str]] = [
            ("top", top_crop, QColor("#F97316"), top_crop.label or "Panel Atas / Wajah"),
        ]
        if layout.mode in ("split_screen", "multi_speaker_split", "blurred_frame", "pip_facecam"):
            boxes.append(
                (
                    "bottom",
                    bot_crop,
                    QColor("#06B6D4"),
                    bot_crop.label or "Panel Bawah / Layar",
                )
            )
        return boxes

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        p.fillRect(self.rect(), QColor("#12100E"))
        frame_rect = self._compute_display_rect()
        self._frame_rect = frame_rect

        if self._frame_qimg is not None and not self._frame_qimg.isNull():
            p.drawImage(frame_rect, self._frame_qimg)
        else:
            p.fillRect(frame_rect, QColor("#1C1714"))
            p.setPen(QColor("#A8A29E"))
            p.drawText(
                frame_rect,
                Qt.AlignmentFlag.AlignCenter,
                "Area Sumber Video (16:9)\nPilih Job/Klip atau klik 'Muat Video...' di atas",
            )

        p.setPen(QPen(QColor("#3D2F24"), 1.5))
        p.drawRect(frame_rect)

        label_font = QFont("Segoe UI", 9)
        label_font.setBold(True)
        p.setFont(label_font)

        # Draw bottom box first so top box remains clearly accessible on overlap
        for _name, crop, color, title in reversed(self._active_boxes()):
            r = self._box_to_widget_rect(crop, frame_rect)
            fill = QColor(color)
            fill.setAlpha(36)
            p.fillRect(r, fill)

            p.setPen(QPen(color, 2.2))
            p.drawRect(r)

            # Tag pill
            tag_rect = QRectF(r.x() + 4, max(frame_rect.y() + 2, r.y() + 4), min(r.width() - 8, 170.0), 20.0)
            tag_bg = QColor(color)
            tag_bg.setAlpha(215)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tag_bg)
            p.drawRoundedRect(tag_rect, 4.0, 4.0)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, title[:22])

            # 4 corner handles
            hs = 9.0
            p.setBrush(QColor("#FFFFFF"))
            p.setPen(QPen(color, 1.5))
            for hx, hy in (
                (r.left(), r.top()),
                (r.right(), r.top()),
                (r.left(), r.bottom()),
                (r.right(), r.bottom()),
            ):
                p.drawRect(QRectF(hx - hs * 0.5, hy - hs * 0.5, hs, hs))

        p.end()

    def _hit_test(self, pos: QPointF) -> tuple[Optional[str], Optional[str]]:
        if self._frame_rect.width() <= 1 or self._frame_rect.height() <= 1:
            return None, None
        handle_tol = 11.0
        for name, crop, _col, _title in self._active_boxes():
            r = self._box_to_widget_rect(crop, self._frame_rect)
            corners = (
                ("nw", QPointF(r.left(), r.top())),
                ("ne", QPointF(r.right(), r.top())),
                ("sw", QPointF(r.left(), r.bottom())),
                ("se", QPointF(r.right(), r.bottom())),
            )
            for mode, cp in corners:
                if abs(pos.x() - cp.x()) <= handle_tol and abs(pos.y() - cp.y()) <= handle_tol:
                    return name, mode
            if r.contains(pos):
                return name, "move"
        return None, None

    def mousePressEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is None or self._config is None or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        name, mode = self._hit_test(pos)
        if name and mode:
            top_crop, bot_crop = self._config.layout.resolve_crops_at_time(self._rel_time_sec)
            crop = top_crop if name == "top" else bot_crop
            self._drag_target = name
            self._drag_mode = mode
            self._drag_start_pt = pos
            self._drag_orig_box = (crop.x, crop.y, crop.w, crop.h)
            self.setCursor(Qt.CursorShape.ClosedHandCursor if mode == "move" else Qt.CursorShape.CrossCursor)

    def mouseMoveEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is None or self._config is None:
            return
        pos = event.position()
        if self._drag_target is None or self._drag_mode is None:
            _name, mode = self._hit_test(pos)
            if mode in ("nw", "se"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif mode in ("ne", "sw"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif mode == "move":
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        fw = max(1.0, self._frame_rect.width())
        fh = max(1.0, self._frame_rect.height())
        dx = (pos.x() - self._drag_start_pt.x()) / fw
        dy = (pos.y() - self._drag_start_pt.y()) / fh

        ox, oy, ow, oh = self._drag_orig_box
        x1, y1, x2, y2 = ox, oy, ox + ow, oy + oh
        min_sz = 0.08

        if self._drag_mode == "move":
            nx = max(0.0, min(1.0 - ow, ox + dx))
            ny = max(0.0, min(1.0 - oh, oy + dy))
            nw, nh = ow, oh
        else:
            if "w" in self._drag_mode:
                x1 = max(0.0, min(x2 - min_sz, ox + dx))
            if "e" in self._drag_mode:
                x2 = min(1.0, max(x1 + min_sz, ox + ow + dx))
            if "n" in self._drag_mode:
                y1 = max(0.0, min(y2 - min_sz, oy + dy))
            if "s" in self._drag_mode:
                y2 = min(1.0, max(y1 + min_sz, oy + oh + dy))
            nx, ny, nw, nh = x1, y1, x2 - x1, y2 - y1

        crop = (
            self._config.layout.top_crop
            if self._drag_target == "top"
            else self._config.layout.bottom_crop
        )
        crop.auto_detect = False
        crop.x = round(nx, 4)
        crop.y = round(ny, 4)
        crop.w = round(nw, 4)
        crop.h = round(nh, 4)
        self._config.layout.framing_tracking_mode = "manual_crop"
        self.update()
        self.crop_changed.emit()

    def mouseReleaseEvent(self, _event: Optional[QMouseEvent]) -> None:
        self._drag_target = None
        self._drag_mode = None
        self.setCursor(Qt.CursorShape.ArrowCursor)


class LivePreviewCanvas9x16(QWidget):
    """Real-time 1:1 WYSIWYG 9:16 (or 16:9) Preview Player supporting overlay drag positioning."""

    overlay_moved = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(270, 480)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._config: Optional[ClipComposerConfig] = None
        self._rendered_qimg: Optional[QImage] = None
        self._canvas_rect = QRectF()

        self._drag_overlay: Optional[str] = None
        self._drag_bubble_idx: int = -1
        self._drag_start_canvas_pt = QPointF()
        self._drag_orig_pos: tuple[int, int] = (540, 1520)

    def render_state(
        self,
        source_frame_bgr: Optional[np.ndarray],
        config: ClipComposerConfig,
        rel_time_sec: float,
        segments: list[TranscriptSegment],
        clip_start_sec: Optional[float] = None,
        clip_end_sec: Optional[float] = None,
    ) -> None:
        self._config = config
        self._rendered_qimg = render_preview_frame(
            source_frame_bgr=source_frame_bgr,
            config=config,
            rel_time_sec=rel_time_sec,
            segments=segments,
            clip_start_sec=clip_start_sec,
            clip_end_sec=clip_end_sec,
        )
        self.update()

    def _compute_canvas_rect(self) -> QRectF:
        cw = self._config.canvas_width if self._config else 1080
        ch = self._config.canvas_height if self._config else 1920
        aspect = cw / max(1, ch)
        avail = QRectF(8.0, 8.0, max(40.0, self.width() - 16.0), max(40.0, self.height() - 16.0))
        if avail.width() / avail.height() > aspect:
            dh = avail.height()
            dw = dh * aspect
        else:
            dw = avail.width()
            dh = dw / aspect
        return QRectF(
            avail.x() + (avail.width() - dw) * 0.5,
            avail.y() + (avail.height() - dh) * 0.5,
            dw,
            dh,
        )

    def _widget_to_logical(self, pos: QPointF) -> QPointF:
        cw = float(self._config.canvas_width if self._config else 1080)
        ch = float(self._config.canvas_height if self._config else 1920)
        r = self._canvas_rect
        if r.width() <= 1 or r.height() <= 1:
            return QPointF(cw * 0.5, ch * 0.5)
        lx = ((pos.x() - r.x()) / r.width()) * cw
        ly = ((pos.y() - r.y()) / r.height()) * ch
        return QPointF(lx, ly)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        p.fillRect(self.rect(), QColor("#12100E"))
        self._canvas_rect = self._compute_canvas_rect()

        if self._rendered_qimg is not None and not self._rendered_qimg.isNull():
            p.drawImage(self._canvas_rect, self._rendered_qimg)
        else:
            p.fillRect(self._canvas_rect, QColor("#1C1714"))

        p.setPen(QPen(QColor("#F97316"), 1.5))
        p.drawRect(self._canvas_rect)
        p.end()

    def mousePressEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is None or self._config is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._canvas_rect.contains(event.position()):
            return
        lp = self._widget_to_logical(event.position())
        self._drag_start_canvas_pt = lp

        # Hit-test comment bubbles first
        for idx, b in enumerate(self._config.overlays.comment_bubbles):
            if b.enabled and abs(lp.x() - b.pos_x) < 340 and abs(lp.y() - b.pos_y) < 110:
                self._drag_overlay = "comment_bubble"
                self._drag_bubble_idx = idx
                self._drag_orig_pos = (b.pos_x, b.pos_y)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return

        # Hit-test hook banner
        hb = self._config.overlays.hook_banner
        if hb.enabled and abs(lp.y() - hb.pos_y) < 100:
            self._drag_overlay = "hook_banner"
            self._drag_orig_pos = (540, hb.pos_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        # Hit-test subtitle anchor
        subs = self._config.subtitles
        if subs.enabled and abs(lp.y() - subs.pos_y) < 130:
            self._drag_overlay = "subtitle"
            self._drag_orig_pos = (subs.pos_x, subs.pos_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is None or self._config is None or self._drag_overlay is None:
            return
        lp = self._widget_to_logical(event.position())
        dx = lp.x() - self._drag_start_canvas_pt.x()
        dy = lp.y() - self._drag_start_canvas_pt.y()
        cw = self._config.canvas_width
        ch = self._config.canvas_height

        if self._drag_overlay == "comment_bubble" and 0 <= self._drag_bubble_idx < len(
            self._config.overlays.comment_bubbles
        ):
            b = self._config.overlays.comment_bubbles[self._drag_bubble_idx]
            b.pos_x = max(100, min(cw - 100, int(round(self._drag_orig_pos[0] + dx))))
            b.pos_y = max(80, min(ch - 120, int(round(self._drag_orig_pos[1] + dy))))
            self.overlay_moved.emit()
        elif self._drag_overlay == "hook_banner":
            self._config.overlays.hook_banner.pos_y = max(
                60, min(ch - 170, int(round(self._drag_orig_pos[1] + dy)))
            )
            self.overlay_moved.emit()
        elif self._drag_overlay == "subtitle":
            self._config.subtitles.placement_preset = "custom"
            self._config.subtitles.pos_x = max(60, min(cw - 60, int(round(self._drag_orig_pos[0] + dx))))
            self._config.subtitles.pos_y = max(120, min(ch - 80, int(round(self._drag_orig_pos[1] + dy))))
            self.overlay_moved.emit()

    def mouseReleaseEvent(self, _event: Optional[QMouseEvent]) -> None:
        self._drag_overlay = None
        self._drag_bubble_idx = -1
        self.setCursor(Qt.CursorShape.ArrowCursor)
