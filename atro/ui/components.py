"""Reusable Fluent UI components, floating action bar, toasts, empty states, and smooth transitions."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def animate_widget_entrance(
    widget: Optional[QWidget],
    duration_ms: int = 180,
    enabled: bool = True,
) -> None:
    """Apply a smooth non-blocking fade entrance transition (auto-cleaned on completion)."""
    if widget is None or not enabled:
        return
    try:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(max(80, int(duration_ms)))
        anim.setStartValue(0.35)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup() -> None:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)

        anim.finished.connect(_cleanup)
        widget._atro_fade_anim = anim  # type: ignore[attr-defined]
        anim.start()
    except Exception:
        widget.setGraphicsEffect(None)


class ToastBanner(QFrame):
    """Non-blocking floating Fluent toast notification shown at bottom-right of host window."""

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
    """Clean friendly empty-state card with 1 clear CTA for beginners."""

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
