"""Async subprocess execution helpers with cancellation and Windows windowless support."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional


class JobCancelledError(Exception):
    """Raised when an active job is cancelled by the user."""


def get_windows_creation_flags() -> int:
    """Suppress console windows when spawning CLI binaries from a frozen GUI app."""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    """Terminate subprocess gracefully, falling back to force kill."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
            return
        except asyncio.TimeoutError:
            pass
        proc.kill()
        await proc.wait()
    except ProcessLookupError:
        pass


async def _read_line_chunked(stream: asyncio.StreamReader) -> Optional[str]:
    """Read next line terminated by either \\n or \\r so progress bars stream smoothly."""
    buf = bytearray()
    while True:
        chunk = await stream.read(1)
        if not chunk:
            if not buf:
                return None
            return buf.decode("utf-8", errors="replace").strip()
        if chunk in (b"\n", b"\r"):
            if buf:
                return buf.decode("utf-8", errors="replace").strip()
            continue
        buf.extend(chunk)


async def run_subprocess_streaming(
    cmd: list[str],
    on_line: Optional[Callable[[str], Awaitable[None] | None]] = None,
    cancel_event: Optional[asyncio.Event] = None,
    cwd: Optional[Path] = None,
) -> tuple[int, list[str]]:
    """Run subprocess asynchronously while streaming output lines and watching cancel_event."""
    if cancel_event and cancel_event.is_set():
        raise JobCancelledError("Job dibatalkan sebelum proses dimulai.")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(cwd) if cwd else None,
        creationflags=get_windows_creation_flags(),
    )

    lines: list[str] = []
    assert proc.stdout is not None

    async def _watch_cancel() -> None:
        if cancel_event is None:
            return
        await cancel_event.wait()
        await _terminate_process(proc)

    cancel_task = asyncio.create_task(_watch_cancel()) if cancel_event is not None else None

    try:
        while True:
            if cancel_event and cancel_event.is_set():
                await _terminate_process(proc)
                raise JobCancelledError("Proses dibatalkan oleh pengguna.")

            line = await _read_line_chunked(proc.stdout)
            if line is None:
                break
            if not line:
                continue
            lines.append(line)
            if on_line is not None:
                res = on_line(line)
                if asyncio.iscoroutine(res):
                    await res

        rc = await proc.wait()
        if cancel_event and cancel_event.is_set():
            raise JobCancelledError("Proses dibatalkan oleh pengguna.")
        return rc, lines
    finally:
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
            try:
                await cancel_task
            except asyncio.CancelledError:
                pass
        if proc.returncode is None:
            await _terminate_process(proc)


async def run_subprocess_capture(
    cmd: list[str],
    timeout: Optional[float] = 30.0,
    cwd: Optional[Path] = None,
) -> tuple[int, str, str]:
    """Run a short-lived subprocess and capture stdout/stderr with optional timeout."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        creationflags=get_windows_creation_flags(),
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _terminate_process(proc)
        raise TimeoutError(f"Command timeout setelah {timeout}s: {' '.join(cmd)}") from exc

    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace").strip(),
        stderr_b.decode("utf-8", errors="replace").strip(),
    )
