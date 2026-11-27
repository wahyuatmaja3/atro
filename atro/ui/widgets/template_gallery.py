"""Filterable Template Gallery Widget with categories, search, favorites, and lazy WYSIWYG thumbnails."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from atro.core.presets import (
    READY_TEMPLATES,
    TEMPLATE_CATEGORIES,
    reload_ready_templates,
    render_template_thumbnail_qimage,
)
from atro.ui.widgets.clip_studio import ReadyTemplateCard


class TemplateGalleryWidget(QWidget):
    """Searchable multi-category gallery hosting 25+ dynamic template cards."""

    template_selected = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)

    def __init__(
        self,
        columns: int = 2,
        favorites: Optional[list[str]] = None,
        recommended: Optional[list[str]] = None,
        scroll_max_height: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._columns = max(1, columns)
        self._favorites: set[str] = set(favorites or [])
        self._recommended: list[str] = list(recommended or [])
        self._selected_id: str = "podcast"
        self.cards: dict[str, ReadyTemplateCard] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Cari template (misal: Podcast, Split, Glass, Religi)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._relayout_filtered_cards)

        self.category_combo = QComboBox()
        self.category_combo.addItem("Semua Kategori (30+)", "__ALL__")
        self.category_combo.addItem("Disarankan AI", "__REC__")
        self.category_combo.addItem("Favorit ★", "__FAV__")
        for cat in TEMPLATE_CATEGORIES:
            self.category_combo.addItem(cat, cat)
        self.category_combo.currentIndexChanged.connect(self._relayout_filtered_cards)

        filter_row.addWidget(self.search_input, 1)
        filter_row.addWidget(self.category_combo)
        root.addLayout(filter_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        if scroll_max_height is not None:
            self._scroll.setMinimumHeight(min(300, scroll_max_height))
            self._scroll.setMaximumHeight(scroll_max_height)

        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(2, 2, 2, 2)
        self._grid.setSpacing(8)
        self._scroll.setWidget(self._grid_host)
        root.addWidget(self._scroll, 1)

        self._thumb_queue: list[str] = []
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(18)
        self._thumb_timer.timeout.connect(self._render_next_thumb_batch)

        self.rebuild_from_registry()

    def rebuild_from_registry(self) -> None:
        """Rebuild cards from READY_TEMPLATES and enqueue lazy WYSIWYG thumbnail rendering."""
        reload_ready_templates()
        self._thumb_timer.stop()
        for c in list(self.cards.values()):
            self._grid.removeWidget(c)
            c.deleteLater()
        self.cards.clear()

        for tid, meta in READY_TEMPLATES.items():
            card = ReadyTemplateCard(
                template_id=tid,
                badge_text=str(meta.get("badge", "Template")),
                title=str(meta.get("title", tid)),
                description=str(meta.get("description", "")),
                category=str(meta.get("category", "Podcast")),
                is_favorite=(tid in self._favorites),
                is_recommended=(tid in self._recommended),
                show_thumb=True,
            )
            card.clicked.connect(self._on_card_clicked)
            card.favorite_toggled.connect(self._on_card_favorite_toggled)
            card.set_selected(tid == self._selected_id)
            self.cards[tid] = card

        self._thumb_queue = list(self.cards.keys())
        self._relayout_filtered_cards()
        if self._thumb_queue:
            self._thumb_timer.start()

    def _render_next_thumb_batch(self) -> None:
        for _ in range(2):
            if not self._thumb_queue:
                self._thumb_timer.stop()
                return
            tid = self._thumb_queue.pop(0)
            card = self.cards.get(tid)
            if card is None:
                continue
            try:
                qimg = render_template_thumbnail_qimage(tid, width=120, height=214)
                card.set_thumbnail_image(qimg)
            except Exception:
                continue

    def _on_card_clicked(self, tid: str) -> None:
        self.set_selected(tid)
        self.template_selected.emit(tid)

    def _on_card_favorite_toggled(self, tid: str, is_fav: bool) -> None:
        if is_fav:
            self._favorites.add(tid)
        else:
            self._favorites.discard(tid)
        self.favorite_toggled.emit(tid, is_fav)
        if self.category_combo.currentData() == "__FAV__":
            self._relayout_filtered_cards()

    def set_selected(self, tid: str) -> None:
        self._selected_id = tid
        for k, card in self.cards.items():
            card.set_selected(k == tid)

    def set_recommended(self, rec_ids: list[str]) -> None:
        self._recommended = list(rec_ids)
        rec_set = set(rec_ids)
        for tid, card in self.cards.items():
            card.set_recommended(tid in rec_set)
        if self.category_combo.currentData() == "__REC__":
            self._relayout_filtered_cards()

    def set_favorites(self, fav_ids: list[str]) -> None:
        self._favorites = set(fav_ids)
        for tid, card in self.cards.items():
            card.set_favorite(tid in self._favorites)

    def _relayout_filtered_cards(self) -> None:
        q = self.search_input.text().strip().lower()
        cat = str(self.category_combo.currentData() or "__ALL__")

        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().setVisible(False)

        visible_idx = 0
        for tid, card in self.cards.items():
            if cat == "__REC__" and tid not in self._recommended:
                continue
            if cat == "__FAV__" and tid not in self._favorites:
                continue
            if cat not in ("__ALL__", "__REC__", "__FAV__") and card.category != cat:
                continue
            if q and q not in card.search_blob:
                continue
            card.setVisible(True)
            self._grid.addWidget(card, visible_idx // self._columns, visible_idx % self._columns)
            visible_idx += 1
