"""Startup preflight check for required external binaries (yt-dlp, ffmpeg, ffprobe)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from atro.config import AppSettings
from atro.core.schemas import PreflightBinaryInfo, PreflightReport
from atro.core.subprocess_utils import get_windows_creation_flags


def resolve_binary(name: str, bin_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate binary in local bin_dir first, then fall back to system PATH."""
    candidates = [f"{name}.exe", name] if os.name == "nt" else [name, f"{name}.exe"]

    if bin_dir and bin_dir.is_dir():
        for filename in candidates:
            candidate = bin_dir / filename
            if candidate.is_file():
                return candidate.resolve()

    for filename in candidates:
        found = shutil.which(filename)
        if found:
            return Path(found).resolve()

    return None


def _probe_binary_sync(name: str, version_arg: str, bin_dir: Optional[Path]) -> PreflightBinaryInfo:
    path = resolve_binary(name, bin_dir)
    if path is None:
        return PreflightBinaryInfo(
            name=name,
            available=False,
            error=f"Binary '{name}' tidak ditemukan di '{bin_dir}' maupun PATH sistem.",
        )

    try:
        completed = subprocess.run(
            [str(path), version_arg],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=get_windows_creation_flags(),
        )
        output = (completed.stdout or completed.stderr or "").strip()
        first_line = output.splitlines()[0].strip() if output else ""
        if completed.returncode != 0:
            return PreflightBinaryInfo(
                name=name,
                available=False,
                path=str(path),
                error=f"'{name}' gagal dijalankan (exit {completed.returncode}): {first_line}",
            )
        return PreflightBinaryInfo(
            name=name,
            available=True,
            path=str(path),
            version=first_line or "detected",
        )
    except Exception as exc:
        return PreflightBinaryInfo(
            name=name,
            available=False,
            path=str(path),
            error=f"Gagal mengeksekusi '{name}': {exc}",
        )


def check_startup_dependencies(settings: AppSettings) -> PreflightReport:
    """Synchronously check availability of yt-dlp, ffmpeg, ffprobe, and API key status."""
    yt_dlp_info = _probe_binary_sync("yt-dlp", "--version", settings.bin_dir)
    ffmpeg_info = _probe_binary_sync("ffmpeg", "-version", settings.bin_dir)
    ffprobe_info = _probe_binary_sync("ffprobe", "-version", settings.bin_dir)

    errors: list[str] = []
    warnings: list[str] = []

    if not yt_dlp_info.available:
        errors.append(
            yt_dlp_info.error
            or "yt-dlp tidak tersedia. Letakkan yt-dlp.exe di folder bin/ atau tambahkan ke PATH."
        )
    if not ffmpeg_info.available:
        errors.append(
            ffmpeg_info.error
            or "ffmpeg tidak tersedia. Letakkan ffmpeg.exe di folder bin/ atau tambahkan ke PATH."
        )
    if not ffprobe_info.available:
        warnings.append(
            ffprobe_info.error
            or "ffprobe tidak ditemukan; metadata video akan menggunakan fallback."
        )

    ai_ready, ai_msg = settings.is_active_provider_ready()
    if not ai_ready:
        warnings.append(ai_msg)

    return PreflightReport(
        ok=(yt_dlp_info.available and ffmpeg_info.available),
        yt_dlp=yt_dlp_info,
        ffmpeg=ffmpeg_info,
        ffprobe=ffprobe_info,
        errors=errors,
        warnings=warnings,
    )


async def check_startup_dependencies_async(settings: AppSettings) -> PreflightReport:
    """Async wrapper for startup dependency checks."""
    return await asyncio.to_thread(check_startup_dependencies, settings)
