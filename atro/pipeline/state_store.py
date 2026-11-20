"""Atomic JSON persistence for per-job state.json files."""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from atro.core.schemas import JobArtifacts, JobRequest, JobState, JobStatus

_ACTIVE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.DOWNLOADING,
    JobStatus.TRANSCRIBING,
    JobStatus.ANALYZING,
    JobStatus.CUTTING,
}
_STATE_WRITE_LOCK = threading.Lock()


def generate_job_id() -> str:
    """Create sortable unique job identifier YYYYMMDD_HHMMSS_xxxxxx."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{stamp}_{suffix}"


def create_job_state(workspace_dir: Path, request: JobRequest, job_id: str | None = None) -> JobState:
    """Initialize directory structure and initial QUEUED JobState."""
    jid = job_id or generate_job_id()
    job_dir = (workspace_dir / jid).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "job.log"

    now = datetime.now().astimezone()
    state = JobState(
        job_id=jid,
        status=JobStatus.QUEUED,
        current_stage="queued",
        progress_pct=0.0,
        created_at=now,
        updated_at=now,
        workspace_dir=str(job_dir),
        log_path=str(log_path),
        request=request,
        artifacts=JobArtifacts(),
        error=None,
    )
    save_state(state)
    return state


def save_state(state: JobState) -> Path:
    """Persist JobState atomically to <workspace_dir>/state.json."""
    job_dir = Path(state.workspace_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    state_path = job_dir / "state.json"
    tmp_path = job_dir / "state.json.tmp"

    updated = state.model_copy(update={"updated_at": datetime.now().astimezone()})
    payload = updated.model_dump_json(indent=2)

    with _STATE_WRITE_LOCK:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, state_path)

    # Keep caller's updated_at synchronized
    state.updated_at = updated.updated_at
    return state_path


def load_state(job_dir: Path) -> JobState:
    """Load JobState from a job directory."""
    state_path = job_dir / "state.json"
    return JobState.model_validate_json(state_path.read_text(encoding="utf-8"))


def list_all_jobs(workspace_dir: Path, recover_stale: bool = True) -> list[JobState]:
    """Scan workspace_dir for all saved jobs, newest first."""
    if not workspace_dir.is_dir():
        return []

    jobs: list[JobState] = []
    for entry in workspace_dir.iterdir():
        if not entry.is_dir():
            continue
        state_path = entry / "state.json"
        if not state_path.is_file():
            continue
        try:
            state = load_state(entry)
        except Exception:
            continue

        if recover_stale and state.status in _ACTIVE_STATUSES:
            state.status = JobStatus.FAILED
            state.error = "Proses terhenti sebelum selesai (gunakan Resume untuk melanjutkan)."
            save_state(state)

        jobs.append(state)

    jobs.sort(key=lambda s: s.created_at, reverse=True)
    return jobs
