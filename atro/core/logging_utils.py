"""Thread-safe per-job file logger with live UI callback forwarding."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


class JobLogger:
    """Writes structured timestamped logs to <job_dir>/job.log and notifies listeners."""

    def __init__(
        self,
        job_id: str,
        log_path: Path,
        on_log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.job_id = job_id
        self.log_path = log_path
        self.on_log = on_log
        self._lock = threading.Lock()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, stage: str, message: str, level: str = "INFO") -> str:
        """Append a log entry to job.log and notify callback."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_msg = message.rstrip("\r\n")
        line = f"[{timestamp}] [{level}] [{stage}] {clean_msg}"

        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()

        if self.on_log is not None:
            try:
                self.on_log(self.job_id, line)
            except Exception:
                pass
        return line

    def info(self, stage: str, message: str) -> str:
        return self.log(stage, message, level="INFO")

    def warning(self, stage: str, message: str) -> str:
        return self.log(stage, message, level="WARN")

    def error(self, stage: str, message: str) -> str:
        return self.log(stage, message, level="ERROR")
