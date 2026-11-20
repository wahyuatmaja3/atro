"""Guard tests ensuring gemini-4-neon never receives sampling parameters and validator sanitizes clips."""

from __future__ import annotations

import pytest

from atro.core.schemas import GeminiClipItem, GeminiClipSelectionSchema, JobRequest
from atro.modules.analyzer.service import build_gemini_config
from atro.modules.analyzer.validator import validate_and_sanitize_clips


def test_gemini_config_forbids_sampling_params() -> None:
    cfg = build_gemini_config("Pilih 3 klip terbaik.")
    dumped = cfg.model_dump(exclude_none=True)

    assert cfg.temperature is None
    assert cfg.top_p is None
    assert cfg.top_k is None
    assert "temperature" not in dumped
    assert "top_p" not in dumped
    assert "top_k" not in dumped
    assert dumped["response_mime_type"] == "application/json"
    assert dumped["response_schema"] is not None


def test_validator_clamps_and_resolves_overlaps() -> None:
    req = JobRequest(
        url="https://youtu.be/abc",
        clip_count=3,
        min_duration_sec=30,
        max_duration_sec=60,
    )
    raw = GeminiClipSelectionSchema(
        clips=[
            GeminiClipItem(
                title="Hook Terbaik",
                start_time=-2.0,  # should clamp to 0.0
                end_time=42.0,
                virality_score=12,  # should clamp to 10
                reason="Pembuka sangat kuat.",
            ),
            GeminiClipItem(
                title="Klip Tumpang Tindih Parah",
                start_time=20.0,
                end_time=58.0,
                virality_score=6,  # lower score, overlaps 22s -> dropped
                reason="Tumpang tindih.",
            ),
            GeminiClipItem(
                title="Klip Overlap Tipis",
                start_time=41.0,  # 1.0s overlap <= 1.5s -> trimmed to 42.0
                end_time=85.0,
                virality_score=9,
                reason="Klimaks emosional.",
            ),
            GeminiClipItem(
                title="Klip Akhir Melebihi Durasi",
                start_time=90.0,
                end_time=150.0,  # video_duration_sec=120 -> clamp to 120.0
                virality_score=8,
                reason="Penutup kuat.",
            ),
        ]
    )

    clips, warnings = validate_and_sanitize_clips(raw, video_duration_sec=120.0, request=req)
    assert len(clips) == 3
    assert clips[0].start_time == 0.0
    assert clips[0].virality_score == 10
    assert clips[1].start_time == 42.0
    assert clips[2].end_time == 120.0
    assert all(c1.end_time <= c2.start_time for c1, c2 in zip(sorted(clips, key=lambda x: x.start_time), sorted(clips, key=lambda x: x.start_time)[1:]))
    assert len(warnings) >= 2


def test_validator_raises_when_empty() -> None:
    req = JobRequest(url="https://youtu.be/abc")
    with pytest.raises(ValueError):
        validate_and_sanitize_clips(GeminiClipSelectionSchema(clips=[]), video_duration_sec=100.0, request=req)
