"""Multi-Track Timeline Editor (Ruler + Video + Subtitle + Overlay + Audio/SFX tracks)."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QFrame, QMenu, QSizePolicy, QWidget

from atro.core.composer_schema import ClipComposerConfig, ShotCropSegment
from atro.core.schemas import TranscriptSegment

_SPEAKER_COLORS: tuple[str, ...] = (
    "#06B6D4",  # SPK_1 Cyan
    "#7C3AED",  # SPK_2 Purple
    "#22C55E",  # SPK_3 Emerald
    "#F59E0B",  # SPK_4 Amber
    "#EC4899",  # SPK_5 Pink
    "#38BDF8",  # SPK_6 Sky
)


def _shot_color(seg: ShotCropSegment, idx: int) -> QColor:
    sid = seg.speaker_id or seg.face_id or ""
    if sid.upper().startswith("SPK_"):
        try:
            k = max(0, int(sid.split("_")[-1]) - 1)
            return QColor(_SPEAKER_COLORS[k % len(_SPEAKER_COLORS)])
        except ValueError:
            pass
    if seg.reason == "reaction":
        return QColor("#F59E0B")
    if seg.reason == "group":
        return QColor("#22C55E")
    return QColor(_SPEAKER_COLORS[idx % len(_SPEAKER_COLORS)])


def _fmt_ruler(sec: float) -> str:
    s = max(0, int(sec))
    m, r = divmod(s, 60)
    return f"{m:02d}:{r:02d}"


class MultiTrackTimelineWidget(QFrame):
    """4-track Timeline with Time Ruler, Video shots, Subtitle, Overlay, Audio/SFX & Playhead."""

    # Signals compatible with FaceLaneTimelineWidget
    time_clicked = pyqtSignal(float)
    split_requested = pyqtSignal(float)
    speaker_assigned = pyqtSignal(int, str)
    pin_speaker_requested = pyqtSignal(str)
    apply_manual_crop_requested = pyqtSignal(int)
    redetect_from_requested = pyqtSignal(float)

    # Studio multi-track signals
    timeline_mutated = pyqtSignal()
    trim_changed = pyqtSignal(float, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TimelinePanel")
        self.setMinimumHeight(148)
        self.setMaximumHeight(176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

        self._shots: list[ShotCropSegment] = []
        self._aliases: dict[str, str] = {}
        self._config: Optional[ClipComposerConfig] = None
        self._transcript_segments: list[TranscriptSegment] = []
        self._clip_start_sec: float = 0.0
        self._duration: float = 15.0
        self._current_time: float = 0.0
        self._pinned_speaker: Optional[str] = None

        self._trim_start: float = 0.0
        self._trim_end: float = 15.0

        self._drag_kind: Optional[str] = None
        self._drag_item_idx: int = -1
        self._drag_orig_start: float = 0.0
        self._drag_orig_end: float = 0.0
        self._drag_start_t: float = 0.0

        self._header_w = 88.0
        self._ruler_h = 22.0
        self._track_h = 28.0
        self._track_gap = 3.0

    def set_state(
        self,
        shots: list[ShotCropSegment],
        duration_sec: float,
        rel_time_sec: float,
        aliases: Optional[dict[str, str]] = None,
        pinned_speaker_id: Optional[str] = None,
    ) -> None:
        """Drop-in replacement for FaceLaneTimelineWidget.set_state."""
        self._shots = list(shots or [])
        old_dur = self._duration
        self._duration = max(0.5, float(duration_sec or 15.0))
        if abs(old_dur - self._duration) > 0.25:
            self._trim_start = 0.0
            self._trim_end = self._duration
        self._current_time = max(0.0, min(self._duration, float(rel_time_sec or 0.0)))
        self._aliases = dict(aliases or {})
        self._pinned_speaker = pinned_speaker_id
        self.update()

    def set_composer_data(
        self,
        config: Optional[ClipComposerConfig],
        transcript_segments: list[TranscriptSegment],
        clip_start_sec: float,
    ) -> None:
        self._config = config
        self._transcript_segments = list(transcript_segments or [])
        self._clip_start_sec = float(clip_start_sec)
        self.update()

    def _track_area_rect(self) -> QRectF:
        return QRectF(
            self._header_w,
            self._ruler_h,
            max(40.0, self.width() - self._header_w - 10.0),
            max(40.0, self.height() - self._ruler_h - 6.0),
        )

    def _x_to_time(self, x: float) -> float:
        area = self._track_area_rect()
        ratio = (x - area.left()) / max(1.0, area.width())
        return max(0.0, min(self._duration, ratio * self._duration))

    def _time_to_x(self, t: float) -> float:
        area = self._track_area_rect()
        ratio = max(0.0, min(1.0, t / max(0.01, self._duration)))
        return area.left() + ratio * area.width()

    def _row_rect(self, row: int) -> QRectF:
        area = self._track_area_rect()
        y = area.top() + row * (self._track_h + self._track_gap)
        return QRectF(area.left(), y, area.width(), self._track_h)

    def _shot_index_at(self, t: float) -> int:
        for i, s in enumerate(self._shots):
            if s.t_start <= t <= s.t_end + 1e-4:
                return i
        return -1

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        p.fillRect(self.rect(), QColor("#0D0F16"))
        area = self._track_area_rect()

        # Left track header column
        p.fillRect(QRectF(0, 0, self._header_w - 2, self.height()), QColor("#12141C"))
        p.setPen(QPen(QColor("#242836"), 1.0))
        p.drawLine(QPointF(self._header_w - 2, 0), QPointF(self._header_w - 2, self.height()))

        # Time Ruler
        ruler_rect = QRectF(area.left(), 0, area.width(), self._ruler_h)
        p.fillRect(ruler_rect, QColor("#12141C"))
        p.setPen(QPen(QColor("#242836"), 1.0))
        p.drawLine(QPointF(area.left(), self._ruler_h - 1), QPointF(area.right(), self._ruler_h - 1))

        small_font = QFont("Segoe UI", 8)
        bold_font = QFont("Segoe UI", 8)
        bold_font.setBold(True)
        p.setFont(small_font)

        step = 2 if self._duration <= 20 else (5 if self._duration <= 60 else 10)
        t_tick = 0.0
        while t_tick <= self._duration + 0.01:
            tx = self._time_to_x(t_tick)
            p.setPen(QPen(QColor("#373E54"), 1.0))
            p.drawLine(QPointF(tx, self._ruler_h - 6), QPointF(tx, self._ruler_h - 1))
            p.setPen(QColor("#9CA3AF"))
            p.drawText(QRectF(tx + 3, 2, 42, 16), Qt.AlignmentFlag.AlignLeft, _fmt_ruler(t_tick))
            t_tick += step

        # 4 Tracks
        tracks = [
            ("Video", "#06B6D4"),
            ("Subtitle", "#10B981"),
            ("Overlay", "#7C3AED"),
            ("Audio/SFX", "#EC4899"),
        ]
        for r_idx, (label, dot_hex) in enumerate(tracks):
            rr = self._row_rect(r_idx)
            p.fillRect(rr, QColor("#141722" if r_idx % 2 == 0 else "#11141D"))
            p.setPen(QPen(QColor("#1F2332"), 1.0))
            p.drawRect(rr)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(dot_hex))
            p.drawEllipse(QRectF(10, rr.center().y() - 3.5, 7, 7))
            p.setFont(bold_font)
            p.setPen(QColor("#D1D5DB"))
            p.drawText(
                QRectF(22, rr.top(), self._header_w - 28, rr.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

        # ---- Row 0: VIDEO Track ----
        r0 = self._row_rect(0)
        if self._shots:
            for i, s in enumerate(self._shots):
                x1 = self._time_to_x(s.t_start)
                x2 = self._time_to_x(min(self._duration, s.t_end))
                sr = QRectF(x1 + 1, r0.top() + 3, max(8.0, x2 - x1 - 2), r0.height() - 6)
                is_cur = s.t_start <= self._current_time <= s.t_end
                col = _shot_color(s, i)
                fill = col.lighter(115) if is_cur else col
                fill.setAlpha(220 if is_cur else 155)
                p.setBrush(fill)
                p.setPen(QPen(QColor("#FFFFFF" if is_cur else "#090A0F"), 1.2))
                p.drawRoundedRect(sr, 4.0, 4.0)
                if sr.width() >= 40:
                    raw_id = s.speaker_id or s.face_id or f"#{i + 1}"
                    lbl = self._aliases.get(raw_id, raw_id)
                    if self._pinned_speaker and raw_id == self._pinned_speaker:
                        lbl = f"📌 {lbl}"
                    p.setFont(bold_font)
                    p.setPen(QColor("#061319" if is_cur else "#F3F4F6"))
                    p.drawText(sr.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, lbl[:16])
        else:
            tx1 = self._time_to_x(self._trim_start)
            tx2 = self._time_to_x(self._trim_end)
            vr = QRectF(tx1 + 1, r0.top() + 3, max(12.0, tx2 - tx1 - 2), r0.height() - 6)
            p.setBrush(QColor(6, 182, 212, 165))
            p.setPen(QPen(QColor("#06B6D4"), 1.2))
            p.drawRoundedRect(vr, 4.0, 4.0)
            p.setFont(bold_font)
            p.setPen(QColor("#F3F4F6"))
            p.drawText(vr.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, "Video Utama (Klik kanan untuk Split/Tag)")

        for hx in (self._time_to_x(self._trim_start), self._time_to_x(self._trim_end)):
            p.setBrush(QColor("#FFFFFF"))
            p.setPen(QPen(QColor("#06B6D4"), 1.4))
            p.drawRoundedRect(QRectF(hx - 3, r0.top() + 2, 6, r0.height() - 4), 2.0, 2.0)

        # ---- Row 1: SUBTITLE Track ----
        r1 = self._row_rect(1)
        if self._config is None or self._config.subtitles.enabled:
            for seg in self._transcript_segments:
                rs = seg.start - self._clip_start_sec
                re = seg.end - self._clip_start_sec
                if re <= 0.0 or rs >= self._duration:
                    continue
                x1 = self._time_to_x(max(0.0, rs))
                x2 = self._time_to_x(min(self._duration, re))
                br = QRectF(x1 + 1, r1.top() + 4, max(6.0, x2 - x1 - 2), r1.height() - 8)
                p.setBrush(QColor(16, 185, 129, 155))
                p.setPen(QPen(QColor("#10B981"), 1.0))
                p.drawRoundedRect(br, 3.0, 3.0)
                if br.width() > 38:
                    p.setFont(small_font)
                    p.setPen(QColor("#ECFDF5"))
                    p.drawText(br.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, seg.text[:22])

        # ---- Row 2: OVERLAY Track ----
        r2 = self._row_rect(2)
        if self._config is not None:
            hb = self._config.overlays.hook_banner
            if hb.enabled and hb.text.strip():
                hx1 = self._time_to_x(0.0)
                hx2 = self._time_to_x(min(self._duration, hb.duration_sec))
                hr = QRectF(hx1 + 1, r2.top() + 3, max(12.0, hx2 - hx1 - 2), r2.height() - 6)
                p.setBrush(QColor(124, 58, 237, 175))
                p.setPen(QPen(QColor("#A78BFA"), 1.2))
                p.drawRoundedRect(hr, 4.0, 4.0)
                if hr.width() > 34:
                    p.setFont(bold_font)
                    p.setPen(QColor("#FFFFFF"))
                    p.drawText(hr.adjusted(5, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, f"Hook: {hb.text[:18]}")
                p.setBrush(QColor("#FFFFFF"))
                p.drawRect(QRectF(hx2 - 3, hr.top() + 2, 5, hr.height() - 4))

            for b in self._config.overlays.comment_bubbles:
                if not b.enabled:
                    continue
                bx1 = self._time_to_x(b.start_sec)
                bx2 = self._time_to_x(min(self._duration, b.end_sec))
                br = QRectF(bx1 + 1, r2.top() + 5, max(12.0, bx2 - bx1 - 2), r2.height() - 10)
                p.setBrush(QColor(245, 158, 11, 185))
                p.setPen(QPen(QColor("#FBBF24"), 1.2))
                p.drawRoundedRect(br, 4.0, 4.0)
                if br.width() > 30:
                    p.setFont(small_font)
                    p.setPen(QColor("#1F1205"))
                    p.drawText(br.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, f"@{b.username}"[:14])

        # ---- Row 3: AUDIO / SFX Track ----
        r3 = self._row_rect(3)
        if self._config is not None:
            if self._config.audio.backsound_enabled:
                bx1 = self._time_to_x(0.0)
                bx2 = self._time_to_x(self._duration)
                ar = QRectF(bx1 + 1, r3.top() + 5, max(16.0, bx2 - bx1 - 2), r3.height() - 10)
                p.setBrush(QColor(236, 72, 153, 85))
                p.setPen(QPen(QColor("#EC4899"), 1.0))
                p.drawRoundedRect(ar, 4.0, 4.0)
                p.setFont(small_font)
                p.setPen(QColor("#FBCFE8"))
                p.drawText(ar.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, "BGM")

            for cue in self._config.audio.sfx_cues:
                cx = self._time_to_x(cue.time_sec)
                p.setBrush(QColor("#F472B6"))
                p.setPen(QPen(QColor("#FFFFFF"), 1.2))
                dia = QPainterPath()
                cy = r3.center().y()
                dia.moveTo(cx, cy - 8)
                dia.lineTo(cx + 6, cy)
                dia.lineTo(cx, cy + 8)
                dia.lineTo(cx - 6, cy)
                dia.closeSubpath()
                p.drawPath(dia)

        # ---- Vertical Playhead Line & Top Handle ----
        px = self._time_to_x(self._current_time)
        p.setPen(QPen(QColor("#FFFFFF"), 2.0))
        p.drawLine(QPointF(px, 2), QPointF(px, self.height() - 2))
        head = QPainterPath()
        head.moveTo(px - 6, 2)
        head.lineTo(px + 6, 2)
        head.lineTo(px + 6, 10)
        head.lineTo(px, 16)
        head.lineTo(px - 6, 10)
        head.closeSubpath()
        p.setBrush(QColor("#7C3AED"))
        p.setPen(QPen(QColor("#FFFFFF"), 1.2))
        p.drawPath(head)

        p.end()

    def _hit_test(self, pos: QPointF) -> tuple[str, int]:
        if pos.x() < self._header_w:
            return "none", -1

        r0 = self._row_rect(0)
        if r0.contains(pos):
            if abs(pos.x() - self._time_to_x(self._trim_start)) <= 7:
                return "trim_l", -1
            if abs(pos.x() - self._time_to_x(self._trim_end)) <= 7:
                return "trim_r", -1
            return "scrub", -1

        r2 = self._row_rect(2)
        if r2.contains(pos) and self._config is not None:
            for bi, b in enumerate(self._config.overlays.comment_bubbles):
                if not b.enabled:
                    continue
                x1 = self._time_to_x(b.start_sec)
                x2 = self._time_to_x(min(self._duration, b.end_sec))
                if abs(pos.x() - x1) <= 6:
                    return "bubble_l", bi
                if abs(pos.x() - x2) <= 6:
                    return "bubble_r", bi
                if x1 <= pos.x() <= x2:
                    return "bubble_move", bi
            hb = self._config.overlays.hook_banner
            if hb.enabled:
                hx2 = self._time_to_x(min(self._duration, hb.duration_sec))
                if abs(pos.x() - hx2) <= 7:
                    return "hook_end", -1

        r3 = self._row_rect(3)
        if r3.contains(pos) and self._config is not None:
            for si, cue in enumerate(self._config.audio.sfx_cues):
                if abs(pos.x() - self._time_to_x(cue.time_sec)) <= 8:
                    return "sfx_move", si

        return "scrub", -1

    def mousePressEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        if pos.x() < self._header_w:
            return
        kind, idx = self._hit_test(pos)
        self._drag_kind = kind
        self._drag_item_idx = idx
        t = self._x_to_time(pos.x())
        self._drag_start_t = t

        if kind == "bubble_move" and self._config and 0 <= idx < len(self._config.overlays.comment_bubbles):
            b = self._config.overlays.comment_bubbles[idx]
            self._drag_orig_start = b.start_sec
            self._drag_orig_end = b.end_sec
        elif kind == "scrub":
            self._current_time = t
            self.update()
            self.time_clicked.emit(round(t, 3))

    def mouseMoveEvent(self, event: Optional[QMouseEvent]) -> None:
        if event is None:
            return
        pos = event.position()
        if self._drag_kind is None:
            kind, _ = self._hit_test(pos)
            if kind in ("trim_l", "trim_r", "hook_end", "bubble_l", "bubble_r"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif kind in ("bubble_move", "sfx_move"):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            r0 = self._row_rect(0)
            if r0.contains(pos) and self._shots:
                t = self._x_to_time(pos.x())
                idx = self._shot_index_at(t)
                if 0 <= idx < len(self._shots):
                    s = self._shots[idx]
                    spk = s.speaker_id or s.face_id or f"Angle #{idx + 1}"
                    alias = self._aliases.get(spk, spk)
                    self.setToolTip(
                        f"{alias} [{s.t_start:.1f}s - {s.t_end:.1f}s] • {s.reason} ({int(round(s.confidence * 100))}%)"
                    )
            return

        t = self._x_to_time(pos.x())
        if self._drag_kind == "scrub":
            self._current_time = t
            self.update()
            self.time_clicked.emit(round(t, 3))
        elif self._drag_kind == "trim_l":
            self._trim_start = max(0.0, min(self._trim_end - 1.0, t))
            self.update()
            self.trim_changed.emit(round(self._trim_start, 2), round(self._trim_end, 2))
        elif self._drag_kind == "trim_r":
            self._trim_end = min(self._duration, max(self._trim_start + 1.0, t))
            self.update()
            self.trim_changed.emit(round(self._trim_start, 2), round(self._trim_end, 2))
        elif self._drag_kind == "hook_end" and self._config is not None:
            self._config.overlays.hook_banner.duration_sec = round(max(1.0, min(self._duration, t)), 2)
            self.update()
            self.timeline_mutated.emit()
        elif self._config is not None and 0 <= self._drag_item_idx < len(self._config.overlays.comment_bubbles):
            b = self._config.overlays.comment_bubbles[self._drag_item_idx]
            if self._drag_kind == "bubble_l":
                b.start_sec = round(max(0.0, min(b.end_sec - 0.5, t)), 2)
            elif self._drag_kind == "bubble_r":
                b.end_sec = round(min(self._duration, max(b.start_sec + 0.5, t)), 2)
            elif self._drag_kind == "bubble_move":
                dur = self._drag_orig_end - self._drag_orig_start
                dt = t - self._drag_start_t
                ns = max(0.0, min(self._duration - dur, self._drag_orig_start + dt))
                b.start_sec = round(ns, 2)
                b.end_sec = round(ns + dur, 2)
            self.update()
            self.timeline_mutated.emit()
        elif self._drag_kind == "sfx_move" and self._config is not None and 0 <= self._drag_item_idx < len(
            self._config.audio.sfx_cues
        ):
            cue = self._config.audio.sfx_cues[self._drag_item_idx]
            cue.time_sec = round(max(0.0, min(self._duration, t)), 2)
            self.update()
            self.timeline_mutated.emit()

    def mouseReleaseEvent(self, _event: Optional[QMouseEvent]) -> None:
        self._drag_kind = None
        self._drag_item_idx = -1
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def contextMenuEvent(self, event: Optional[QContextMenuEvent]) -> None:
        if event is None:
            return
        t_sec = round(self._x_to_time(event.pos().x()), 3)
        seg_idx = self._shot_index_at(t_sec)
        menu = QMenu(self)

        split_act = menu.addAction(f"Potong Segmen di {t_sec:.1f}s (Split)")
        apply_crop_act = menu.addAction("Pakai Kotak Crop Manual Ini pada Segmen") if seg_idx >= 0 else None
        spk_menu = menu.addMenu("Tandai Pembicara Segmen Ini") if seg_idx >= 0 else None
        spk_actions: dict[object, str] = {}
        if spk_menu is not None:
            for k in range(1, 6):
                sid = f"SPK_{k}"
                al = self._aliases.get(sid, f"Pembicara {k}")
                act = spk_menu.addAction(f"{sid} ({al})")
                spk_actions[act] = sid

        cur_spk = (self._shots[seg_idx].speaker_id or "SPK_1") if (0 <= seg_idx < len(self._shots)) else "SPK_1"
        pin_act = menu.addAction(f"Pin Fokus ke {self._aliases.get(cur_spk, cur_spk)}") if seg_idx >= 0 else None
        unpin_act = menu.addAction("Lepas Pin Pembicara (Otomatis)") if self._pinned_speaker else None
        redetect_act = menu.addAction(f"Deteksi Ulang Mulai {t_sec:.1f}s")

        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen == split_act:
            self.split_requested.emit(t_sec)
        elif apply_crop_act is not None and chosen == apply_crop_act and seg_idx >= 0:
            self.apply_manual_crop_requested.emit(seg_idx)
        elif chosen in spk_actions and seg_idx >= 0:
            self.speaker_assigned.emit(seg_idx, spk_actions[chosen])
        elif pin_act is not None and chosen == pin_act:
            self.pin_speaker_requested.emit(cur_spk)
        elif unpin_act is not None and chosen == unpin_act:
            self.pin_speaker_requested.emit("")
        elif chosen == redetect_act:
            self.redetect_from_requested.emit(t_sec)
