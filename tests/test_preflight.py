"""Tests for startup binary preflight verification."""

from __future__ import annotations

from pathlib import Path

from atro.config import AppSettings
from atro.core import preflight as preflight_mod


def test_preflight_detects_real_binaries(tmp_path: Path) -> None:
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin")
    report = preflight_mod.check_startup_dependencies(settings)

    assert report.yt_dlp.available is True
    assert report.yt_dlp.path is not None
    assert report.ffmpeg.available is True
    assert report.ffmpeg.path is not None
    assert report.ffprobe.available is True
    assert report.ok is True


def test_preflight_fails_when_binaries_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(preflight_mod, "resolve_binary", lambda name, bin_dir=None: None)
    settings = AppSettings(workspace_dir=tmp_path / "ws", bin_dir=tmp_path / "bin")
    report = preflight_mod.check_startup_dependencies(settings)

    assert report.ok is False
    assert report.yt_dlp.available is False
    assert report.ffmpeg.available is False
    assert len(report.errors) >= 2
