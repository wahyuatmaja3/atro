"""Left Panel of Editor Studio: Interactive word-by-word Transcript & Quick Clip Switcher."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from atro.core.schemas import RenderedClip, TranscriptSegment


def _fmt_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


class _FlowLayout(QLayout):
    """Lightweight Qt FlowLayout wrapping word chips neatly inside scrollable panel."""

    def __init__(self, parent: Optional[QWidget] = None, margin: int = 0, spacing: int = 4) -> None:
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        l, t, r, b = self.getContentsMargins()
        size += QSize(l + r, t + b)
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        l, t, r, b = self.getContentsMargins()
        eff = rect.adjusted(l, t, -r, -b)
        x = eff.x()
        y = eff.y()
        line_h = 0
        for item in self._items:
            sz = item.sizeHint()
            next_x = x + sz.width() + self._spacing
            if next_x - self._spacing > eff.right() and line_h > 0:
                x = eff.x()
                y += line_h + self._spacing
                next_x = x + sz.width() + self._spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            line_h = max(line_h, sz.height())
        return y + line_h - rect.y() + b


class _FlowWidget(QWidget):
    """QWidget host that honors _FlowLayout heightForWidth inside QScrollArea."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        lay = self.layout()
        return lay.heightForWidth(max(120, width)) if lay is not None else super().heightForWidth(width)

    def sizeHint(self) -> QSize:
        w = max(180, self.width() or 210)
        h = self.heightForWidth(w)
        return QSize(w, max(24, h))

    def minimumSizeHint(self) -> QSize:
        return QSize(160, 26)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.updateGeometry()


class _SegmentWordBlock(QFrame):
    """One transcript segment row with clickable word chips & inline editor."""

    seek_requested = pyqtSignal(float)
    text_edited = pyqtSignal(int, str)

    def __init__(
        self,
        seg_index: int,
        rel_start: float,
        rel_end: float,
        words: list[tuple[str, float, float]],
        raw_text: str,
        is_edited: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.seg_index = seg_index
        self.rel_start = rel_start
        self.rel_end = rel_end
        self._raw_text = raw_text
        self._is_edited = is_edited
        self._word_buttons: list[tuple[QPushButton, float, float]] = []

        self.setObjectName("Card")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        ts_btn = QPushButton(_fmt_ts(rel_start))
        ts_btn.setObjectName("SmallAction")
        ts_btn.setStyleSheet("padding: 2px 7px; font-size: 10px; color: #06B6D4;")
        ts_btn.setToolTip(f"Lompat ke {rel_start:.1f}s")
        ts_btn.clicked.connect(lambda: self.seek_requested.emit(self.rel_start))

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setObjectName("GhostButton")
        self.edit_btn.setStyleSheet("padding: 2px 7px; font-size: 10px;")
        self.edit_btn.setToolTip("Edit teks segmen ini (langsung sinkron ke subtitle)")
        self.edit_btn.clicked.connect(self._toggle_inline_edit)

        hdr.addWidget(ts_btn)
        if is_edited:
            ed_pill = QLabel("Edited")
            ed_pill.setObjectName("StatusPill")
            ed_pill.setProperty("tone", "cyan")
            hdr.addWidget(ed_pill)
        hdr.addStretch(1)
        hdr.addWidget(self.edit_btn)
        root.addLayout(hdr)

        self.words_host = _FlowWidget(self)
        flow = _FlowLayout(self.words_host, margin=0, spacing=4)
        for token, wt_s, wt_e in words:
            wb = QPushButton(token)
            wb.setObjectName("WordChip")
            wb.setCursor(Qt.CursorShape.PointingHandCursor)
            wb.setProperty("active", "false")
            wb.setProperty("edited", "true" if is_edited else "false")
            wb.setToolTip(f"Kata pada {wt_s:.1f}s – Klik untuk lompat")
            wb.clicked.connect(lambda _chk=False, t=wt_s: self.seek_requested.emit(t))
            flow.addWidget(wb)
            self._word_buttons.append((wb, wt_s, wt_e))
        root.addWidget(self.words_host)

        self.inline_edit = QLineEdit(raw_text)
        self.inline_edit.setVisible(False)
        self.inline_edit.setPlaceholderText("Edit kalimat segmen ini lalu tekan Enter...")
        self.inline_edit.returnPressed.connect(self._commit_inline_edit)
        root.addWidget(self.inline_edit)

    def _toggle_inline_edit(self) -> None:
        editing = not self.inline_edit.isVisible()
        self.inline_edit.setVisible(editing)
        self.words_host.setVisible(not editing)
        self.edit_btn.setText("Simpan" if editing else "Edit")
        if editing:
            self.inline_edit.setFocus()
            self.inline_edit.selectAll()
        else:
            self._commit_inline_edit()

    def _commit_inline_edit(self) -> None:
        new_txt = self.inline_edit.text().strip()
        self.inline_edit.setVisible(False)
        self.words_host.setVisible(True)
        self.edit_btn.setText("Edit")
        if new_txt != self._raw_text:
            self._raw_text = new_txt
            self.text_edited.emit(self.seg_index, new_txt)

    def highlight_at_time(self, rel_t: float) -> bool:
        hit_any = False
        for wb, ws, we in self._word_buttons:
            is_act = ws <= rel_t <= max(ws + 0.15, we)
            if is_act:
                hit_any = True
            cur = wb.property("active") == "true"
            if cur != is_act:
                wb.setProperty("active", "true" if is_act else "false")
                wb.style().unpolish(wb)
                wb.style().polish(wb)
        return hit_any


class InteractiveTranscriptPanel(QFrame):
    """Left panel of 3-panel Studio containing tabbed interactive Transcript and Clips list."""

    word_seek_requested = pyqtSignal(float)
    transcript_edited = pyqtSignal(int, str)
    clip_selected = pyqtSignal(int)
    reset_transcript_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TranscriptPanel")
        self._blocks: list[_SegmentWordBlock] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.tabs = QTabWidget()

        # ---- Tab 1: Interactive Word Transcript ----
        tr_tab = QWidget()
        tr_lay = QVBoxLayout(tr_tab)
        tr_lay.setContentsMargins(2, 4, 2, 2)
        tr_lay.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Cari kata di transkrip...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_blocks)
        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("SmallAction")
        reset_btn.setToolTip("Kembalikan transkrip ke hasil asli")
        reset_btn.clicked.connect(self.reset_transcript_requested.emit)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(reset_btn)
        tr_lay.addLayout(search_row)

        self.tr_scroll = QScrollArea()
        self.tr_scroll.setWidgetResizable(True)
        self.tr_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tr_container = QWidget()
        self.tr_list_lay = QVBoxLayout(self.tr_container)
        self.tr_list_lay.setContentsMargins(0, 0, 4, 0)
        self.tr_list_lay.setSpacing(8)
        self.tr_list_lay.addStretch(1)
        self.tr_scroll.setWidget(self.tr_container)
        tr_lay.addWidget(self.tr_scroll, 1)

        self.tabs.addTab(tr_tab, "Transcript")

        # ---- Tab 2: Quick Clips Switcher ----
        clips_tab = QWidget()
        clips_lay = QVBoxLayout(clips_tab)
        clips_lay.setContentsMargins(2, 4, 2, 2)
        clips_lay.setSpacing(6)
        clips_hint = QLabel("Pilih klip untuk langsung edit di Studio:")
        clips_hint.setObjectName("MutedText")
        clips_lay.addWidget(clips_hint)

        self.clips_list = QListWidget()
        self.clips_list.setSpacing(4)
        self.clips_list.itemClicked.connect(self._on_clip_item_clicked)
        clips_lay.addWidget(self.clips_list, 1)

        self.tabs.addTab(clips_tab, "Clips")

        root.addWidget(self.tabs, 1)

    def set_clips(self, clips: list, active_clip_index: Optional[int] = None) -> None:
        self.clips_list.clear()
        sel_row = -1
        for i, clip in enumerate(clips):
            c_idx = int(getattr(clip, "clip_index", i + 1))
            dur = float(
                getattr(
                    clip,
                    "output_duration_sec",
                    max(0.0, float(getattr(clip, "end_time", 0.0)) - float(getattr(clip, "start_time", 0.0))),
                )
            )
            score_100 = max(1, min(100, int(round(float(getattr(clip, "virality_score", 7)) * 10))))
            label = f"#{c_idx:02d} • {score_100}/100 ({dur:.0f}s)\n{clip.title}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, c_idx)
            self.clips_list.addItem(item)
            if active_clip_index is not None and c_idx == active_clip_index:
                sel_row = i
        if sel_row >= 0:
            self.clips_list.setCurrentRow(sel_row)

    def _on_clip_item_clicked(self, item: QListWidgetItem) -> None:
        clip_idx = item.data(Qt.ItemDataRole.UserRole)
        if clip_idx is not None:
            self.clip_selected.emit(int(clip_idx))

    def load_segments(
        self,
        segments: list[TranscriptSegment],
        clip_start_sec: float,
        clip_end_sec: float,
        overrides: dict[str, str],
    ) -> None:
        self._blocks.clear()
        while self.tr_list_lay.count() > 1:
            item = self.tr_list_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        token_budget = 0
        for idx, seg in enumerate(segments):
            if seg.end < clip_start_sec - 0.1 or seg.start > clip_end_sec + 0.1:
                continue
            rel_s = max(0.0, seg.start - clip_start_sec)
            rel_e = max(rel_s + 0.2, seg.end - clip_start_sec)
            key = str(idx)
            is_edited = key in overrides
            display_txt = (overrides.get(key) if is_edited else seg.text).strip()
            if not display_txt:
                continue

            words_tuples: list[tuple[str, float, float]] = []
            if not is_edited and seg.words and token_budget < 450:
                for w in seg.words:
                    ws = max(0.0, w.start - clip_start_sec)
                    we = max(ws + 0.1, w.end - clip_start_sec)
                    words_tuples.append((w.word, ws, we))
                    token_budget += 1
            else:
                parts = display_txt.split()
                step = (rel_e - rel_s) / max(1, len(parts))
                for wi, word in enumerate(parts):
                    ws = rel_s + wi * step
                    words_tuples.append((word, ws, ws + step))
                    token_budget += 1

            blk = _SegmentWordBlock(
                seg_index=idx,
                rel_start=rel_s,
                rel_end=rel_e,
                words=words_tuples,
                raw_text=display_txt,
                is_edited=is_edited,
                parent=self.tr_container,
            )
            blk.seek_requested.connect(self.word_seek_requested.emit)
            blk.text_edited.connect(self.transcript_edited.emit)
            self._blocks.append(blk)
            self.tr_list_lay.insertWidget(self.tr_list_lay.count() - 1, blk)

        if not self._blocks:
            empty_lbl = QLabel("Belum ada transkrip dalam rentang klip ini.")
            empty_lbl.setObjectName("MutedText")
            empty_lbl.setWordWrap(True)
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tr_list_lay.insertWidget(0, empty_lbl)

    def _filter_blocks(self, query: str) -> None:
        q = query.strip().lower()
        for blk in self._blocks:
            blk.setVisible((not q) or (q in blk._raw_text.lower()))

    def update_playhead(self, rel_t: float) -> None:
        for blk in self._blocks:
            blk.highlight_at_time(rel_t)
