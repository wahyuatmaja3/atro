"""Read-only monospace live log viewer widget for a selected job."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogViewer(QWidget):
    """Displays job.log contents and streams incoming log lines."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_log_path: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.path_label = QLabel("Belum ada job dipilih")
        self.path_label.setObjectName("MutedText")

        copy_btn = QPushButton("Salin Log")
        copy_btn.setObjectName("SmallAction")
        copy_btn.clicked.connect(self._copy_logs)

        open_btn = QPushButton("Buka job.log")
        open_btn.setObjectName("SmallAction")
        open_btn.clicked.connect(self._open_log_file)

        toolbar.addWidget(self.path_label, 1)
        toolbar.addWidget(copy_btn)
        toolbar.addWidget(open_btn)
        layout.addLayout(toolbar)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("LogEditor")
        self.editor.setReadOnly(True)
        self.editor.setMaximumBlockCount(4000)
        layout.addWidget(self.editor, 1)

    def load_file(self, log_path: Optional[Path]) -> None:
        self._current_log_path = log_path
        self.editor.clear()
        if log_path is None:
            self.path_label.setText("Belum ada job dipilih")
            return
        self.path_label.setText(str(log_path))
        if log_path.is_file():
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
                self.editor.setPlainText(content.rstrip("\n"))
                self._scroll_to_bottom()
            except Exception as exc:
                self.editor.setPlainText(f"Gagal membaca log: {exc}")

    def append_line(self, line: str) -> None:
        self.editor.appendPlainText(line)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        sb = self.editor.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _copy_logs(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.editor.toPlainText())

    def _open_log_file(self) -> None:
        if self._current_log_path and self._current_log_path.is_file() and os.name == "nt":
            os.startfile(str(self._current_log_path))  # type: ignore[attr-defined]
