"""Shared Creator Studio components, vector icons, and Fluent micro-interactions."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def create_vector_icon(name: str, color_hex: str = "#F3F4F6", size: int = 18) -> QIcon:
    """Draw crisp geometric vector UI icons without external SVG dependencies."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    color = QColor(color_hex)
    pen = QPen(color, max(1.4, size / 11.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    m = size * 0.17
    w = size - 2 * m
    cx = size * 0.5
    cy = size * 0.5

    if name == "home":
        path = QPainterPath()
        path.moveTo(m, cy + 0.5)
        path.lineTo(cx, m)
        path.lineTo(size - m, cy + 0.5)
        p.drawPath(path)
        p.drawRoundedRect(QRectF(m + w * 0.16, cy, w * 0.68, w * 0.54), 1.5, 1.5)
    elif name == "templates":
        half = w * 0.43
        gap = w - 2 * half
        p.drawRoundedRect(QRectF(m, m, half, half), 2, 2)
        p.drawRoundedRect(QRectF(m + half + gap, m, half, half), 2, 2)
        p.drawRoundedRect(QRectF(m, m + half + gap, half, half), 2, 2)
        p.drawRoundedRect(QRectF(m + half + gap, m + half + gap, half, half), 2, 2)
    elif name in {"editor", "scissors"}:
        p.drawRoundedRect(QRectF(m, m + 1.5, w, w - 3), 2.5, 2.5)
        p.drawLine(QPointF(cx, m + 1.5), QPointF(cx, size - m - 1.5))
        p.drawLine(QPointF(m + 3, cy), QPointF(cx - 2.5, cy))
    elif name == "queue":
        for row in range(3):
            y = m + row * (w * 0.38) + 1.5
            p.drawPoint(QPointF(m + 1.5, y))
            p.drawLine(QPointF(m + 5.5, y), QPointF(size - m, y))
    elif name == "settings":
        p.drawEllipse(QPointF(cx, cy), w * 0.22, w * 0.22)
        p.drawEllipse(QPointF(cx, cy), w * 0.44, w * 0.44)
    elif name == "play":
        p.setBrush(color)
        path = QPainterPath()
        path.moveTo(m + 2.5, m + 1.0)
        path.lineTo(size - m - 1.0, cy)
        path.lineTo(m + 2.5, size - m - 1.0)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "pause":
        p.setBrush(color)
        bw = max(2.2, w * 0.22)
        p.drawRoundedRect(QRectF(m + w * 0.16, m + 1.0, bw, w - 2.0), 1, 1)
        p.drawRoundedRect(QRectF(size - m - w * 0.16 - bw, m + 1.0, bw, w - 2.0), 1, 1)
    elif name == "sparkles":
        path = QPainterPath()
        path.moveTo(cx, m)
        path.quadTo(cx, cy, size - m, cy)
        path.quadTo(cx, cy, cx, size - m)
        path.quadTo(cx, cy, m, cy)
        path.quadTo(cx, cy, cx, m)
        p.drawPath(path)
    elif name == "upload":
        p.drawLine(QPointF(cx, m + 1), QPointF(cx, size - m - 3))
        p.drawLine(QPointF(cx - w * 0.28, m + w * 0.3), QPointF(cx, m + 1))
        p.drawLine(QPointF(cx + w * 0.28, m + w * 0.3), QPointF(cx, m + 1))
        path = QPainterPath()
        path.moveTo(m, size - m - 3)
        path.lineTo(m, size - m)
        path.lineTo(size - m, size - m)
        path.lineTo(size - m, size - m - 3)
        p.drawPath(path)
    elif name == "download":
        p.drawLine(QPointF(cx, m), QPointF(cx, size - m - 3.5))
        p.drawLine(QPointF(cx - w * 0.28, size - m - w * 0.35 - 2), QPointF(cx, size - m - 3))
        p.drawLine(QPointF(cx + w * 0.28, size - m - w * 0.35 - 2), QPointF(cx, size - m - 3))
        p.drawLine(QPointF(m, size - m), QPointF(size - m, size - m))
    elif name == "chevron_left":
        p.drawLine(QPointF(cx + 2, m + 2), QPointF(cx - 3, cy))
        p.drawLine(QPointF(cx - 3, cy), QPointF(cx + 2, size - m - 2))
    elif name == "chevron_right":
        p.drawLine(QPointF(cx - 2, m + 2), QPointF(cx + 3, cy))
        p.drawLine(QPointF(cx + 3, cy), QPointF(cx - 2, size - m - 2))
    elif name == "back":
        p.drawLine(QPointF(size - m - 1, cy), QPointF(m + 1, cy))
        p.drawLine(QPointF(m + 5, m + 3), QPointF(m + 1, cy))
        p.drawLine(QPointF(m + 5, size - m - 3), QPointF(m + 1, cy))
    elif name == "safe_zone":
        p.drawRoundedRect(QRectF(m + 2, m, w - 4, w), 2, 2)
        p.setPen(QPen(color, max(1.1, size / 14.0), Qt.PenStyle.DashLine))
        p.drawRect(QRectF(m + 4.5, m + 3.0, w - 9.0, w - 6.0))
    elif name == "zoom":
        p.drawEllipse(QPointF(cx - 1.5, cy - 1.5), w * 0.33, w * 0.33)
        p.drawLine(QPointF(cx + w * 0.12, cy + w * 0.12), QPointF(size - m, size - m))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(m, m + 2)
        path.lineTo(m + w * 0.4, m + 2)
        path.lineTo(m + w * 0.54, m + 4.5)
        path.lineTo(size - m, m + 4.5)
        path.lineTo(size - m, size - m - 1)
        path.lineTo(m, size - m - 1)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "captions":
        p.drawRoundedRect(QRectF(m, m + 2, w, w - 4), 2.5, 2.5)
        p.drawLine(QPointF(m + 3, cy - 1), QPointF(cx - 1, cy - 1))
        p.drawLine(QPointF(cx + 1.5, cy - 1), QPointF(size - m - 3, cy - 1))
        p.drawLine(QPointF(m + 3, cy + 3), QPointF(size - m - 4, cy + 3))
    elif name == "music":
        p.drawEllipse(QPointF(m + 3, size - m - 2.5), 2.3, 2.0)
        p.drawEllipse(QPointF(size - m - 2.5, size - m - 4.0), 2.3, 2.0)
        p.drawLine(QPointF(m + 5, size - m - 3), QPointF(m + 5, m + 2.5))
        p.drawLine(QPointF(size - m - 0.5, size - m - 4.5), QPointF(size - m - 0.5, m + 1.0))
        p.drawLine(QPointF(m + 5, m + 2.5), QPointF(size - m - 0.5, m + 1.0))
    elif name in {"layout", "split"}:
        p.drawRoundedRect(QRectF(m, m, w, w), 2, 2)
        p.drawLine(QPointF(m, cy), QPointF(size - m, cy))
    elif name == "face":
        p.drawRoundedRect(QRectF(m + 1, m + 1, w - 2, w - 2), 3, 3)
        p.drawEllipse(QPointF(cx, cy - 1.5), w * 0.18, w * 0.18)
        path = QPainterPath()
        path.moveTo(cx - w * 0.25, size - m - 2)
        path.quadTo(cx, cy + 1.5, cx + w * 0.25, size - m - 2)
        p.drawPath(path)
    elif name == "layers":
        for dy in (-3.2, 0.2, 3.6):
            p.drawLine(QPointF(m + 1, cy + dy), QPointF(size - m - 1, cy + dy))
    elif name == "check":
        p.drawLine(QPointF(m + 1.5, cy), QPointF(cx - 1.0, size - m - 2.5))
        p.drawLine(QPointF(cx - 1.0, size - m - 2.5), QPointF(size - m - 1.0, m + 2.5))
    elif name == "refresh":
        p.drawArc(QRectF(m + 1, m + 1, w - 2, w - 2), 35 * 16, 285 * 16)
        p.drawLine(QPointF(size - m - 1.5, m + 2), QPointF(size - m - 1.5, m + 5.5))
        p.drawLine(QPointF(size - m - 1.5, m + 5.5), QPointF(size - m - 5, m + 5.5))
    elif name == "plus":
        p.drawLine(QPointF(cx, m + 1), QPointF(cx, size - m - 1))
        p.drawLine(QPointF(m + 1, cy), QPointF(size - m - 1, cy))
    else:
        p.drawRoundedRect(QRectF(m + 1, m + 1, w - 2, w - 2), 2.5, 2.5)

    p.end()
    return QIcon(pix)


class ToggleSwitch(QAbstractButton):
    """Sleek Creator Studio pill toggle switch compatible with QCheckBox usage."""

    def __init__(
        self,
        label: str = "",
        checked: bool = False,
        accent_color: str = "#7C3AED",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setText(label)
        self._accent = accent_color
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._track_w = 36
        self._track_h = 20

    def sizeHint(self) -> QSize:
        text = self.text()
        fm = self.fontMetrics()
        tw = (fm.horizontalAdvance(text) + 10) if text else 0
        return QSize(self._track_w + tw + 4, max(24, fm.height() + 4))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        y = (self.height() - self._track_h) / 2.0
        track_rect = QRectF(1.0, y, float(self._track_w), float(self._track_h))
        checked = self.isChecked()
        hovered = self.underMouse()

        if checked:
            bg = QColor(self._accent)
            if hovered:
                bg = bg.lighter(112)
            border = bg
        else:
            bg = QColor("#252A3B" if hovered else "#1C202E")
            border = QColor("#444C66" if hovered else "#32384C")

        p.setPen(QPen(border, 1.2))
        p.setBrush(bg)
        p.drawRoundedRect(track_rect, self._track_h / 2.0, self._track_h / 2.0)

        knob_d = self._track_h - 6.0
        kx = (self._track_w - knob_d - 2.0) if checked else 4.0
        ky = y + 3.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF" if checked else "#9CA3AF"))
        p.drawEllipse(QRectF(kx, ky, knob_d, knob_d))

        if self.text():
            p.setPen(QColor("#F3F4F6" if checked else "#9CA3AF"))
            text_rect = QRectF(
                float(self._track_w + 9),
                0.0,
                float(max(10, self.width() - self._track_w - 10)),
                float(self.height()),
            )
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()


class DurationChipBar(QWidget):
    """Segmented chip selector for quick clip duration presets (30s, 60s, Auto)."""

    duration_selected = pyqtSignal(str)

    def __init__(self, initial: str = "auto", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current = initial
        self._buttons: dict[str, QPushButton] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for key, label, tip in [
            ("30s", "30s", "Klip super pendek & punchy (15–35 detik)"),
            ("60s", "60s", "Klip standar TikTok / Shorts / Reels (35–65 detik)"),
            ("auto", "Auto", "AI pilih durasi optimal sesuai konteks (20–90 detik)"),
        ]:
            btn = QPushButton(label)
            btn.setObjectName("DurationChip")
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, k=key: self.set_duration(k, emit_signal=True))
            self._buttons[key] = btn
            lay.addWidget(btn)
        self.set_duration(initial, emit_signal=False)

    def current_duration(self) -> str:
        return self._current

    def set_duration(self, key: str, emit_signal: bool = False) -> None:
        norm = key if key in self._buttons else "auto"
        self._current = norm
        for k, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.setChecked(k == norm)
            btn.blockSignals(False)
        if emit_signal:
            self.duration_selected.emit(norm)


def animate_widget_entrance(
    widget: Optional[QWidget],
    duration_ms: int = 180,
    enabled: bool = True,
) -> None:
    """Smooth subtle fade-in transition without hiding widget at 0 opacity or leaving stale effects."""
    if widget is None or not enabled or duration_ms <= 0:
        return
    try:
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setOpacity(0.92)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(min(140, duration_ms))
        anim.setStartValue(0.92)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup() -> None:
            try:
                if widget.graphicsEffect() is effect:
                    widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

        anim.finished.connect(_cleanup)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        widget._entrance_anim = anim  # keep ref until finished
    except Exception:
        pass


class ToastBanner(QFrame):
    """Non-blocking floating toast notification shown at bottom-right of host window."""

    dismissed = pyqtSignal()

    def __init__(
        self,
        title: str,
        message: str,
        tone: str = "ok",
        duration_ms: int = 3600,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToastBanner")
        self.setProperty("tone", tone)
        self.setMinimumWidth(300)
        self.setMaximumWidth(420)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 10, 10)
        root.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        m_lbl = QLabel(message)
        m_lbl.setObjectName("MutedText")
        m_lbl.setWordWrap(True)
        text_col.addWidget(t_lbl)
        if message:
            text_col.addWidget(m_lbl)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("SmallAction")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self._close_toast)

        root.addLayout(text_col, 1)
        root.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._close_toast)
        if duration_ms > 0:
            self._timer.start(duration_ms)

    def _close_toast(self) -> None:
        self._timer.stop()
        self.hide()
        self.dismissed.emit()
        self.deleteLater()


class ToastManager:
    """Positions floating ToastBanner notifications cleanly above the bottom-right corner."""

    def __init__(self, host: QWidget) -> None:
        self._host = host
        self._active: Optional[ToastBanner] = None

    def show_toast(
        self,
        title: str,
        message: str = "",
        tone: str = "ok",
        duration_ms: int = 3600,
        reduce_motion: bool = False,
    ) -> ToastBanner:
        if self._active is not None:
            try:
                self._active.hide()
                self._active.deleteLater()
            except Exception:
                pass
            self._active = None

        toast = ToastBanner(
            title=title,
            message=message,
            tone=tone,
            duration_ms=duration_ms,
            parent=self._host,
        )
        toast.adjustSize()
        self._active = toast
        toast.dismissed.connect(lambda: self._clear_ref(toast))
        self.reposition()
        toast.show()
        toast.raise_()
        animate_widget_entrance(toast, duration_ms=170, enabled=not reduce_motion)
        return toast

    def _clear_ref(self, toast: ToastBanner) -> None:
        if self._active is toast:
            self._active = None

    def reposition(self) -> None:
        if self._active is None:
            return
        hw = max(320, self._host.width())
        hh = max(240, self._host.height())
        sz = self._active.sizeHint()
        tw = max(300, min(420, sz.width()))
        th = max(56, sz.height())
        self._active.resize(tw, th)
        self._active.move(QPoint(max(12, hw - tw - 20), max(12, hh - th - 20)))


class FloatingActionBar(QFrame):
    """Sticky elevated bottom bar grouping batch clip actions cleanly."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("FloatingBar")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(14, 9, 14, 9)
        self._layout.setSpacing(10)

    def bar_layout(self) -> QHBoxLayout:
        return self._layout


class EmptyStateCard(QFrame):
    """Clean friendly empty-state card with 1 clear CTA."""

    action_clicked = pyqtSignal()

    def __init__(
        self,
        badge_text: str,
        title: str,
        description: str,
        cta_text: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WinUICard")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 28, 24, 28)
        v.setSpacing(10)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)

        badge = QLabel(badge_text)
        badge.setObjectName("StepBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        t_lbl = QLabel(title)
        t_lbl.setObjectName("HeroTitle")
        t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t_lbl.setWordWrap(True)

        d_lbl = QLabel(description)
        d_lbl.setObjectName("MutedText")
        d_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d_lbl.setWordWrap(True)
        d_lbl.setMaximumWidth(520)

        v.addWidget(badge, 0, Qt.AlignmentFlag.AlignCenter)
        v.addWidget(t_lbl, 0, Qt.AlignmentFlag.AlignCenter)
        v.addWidget(d_lbl, 0, Qt.AlignmentFlag.AlignCenter)

        if cta_text:
            btn = QPushButton(cta_text)
            btn.setObjectName("HeroButton")
            btn.clicked.connect(self.action_clicked.emit)
            v.addSpacing(4)
            v.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)
            self.cta_button: Optional[QPushButton] = btn
        else:
            self.cta_button = None
