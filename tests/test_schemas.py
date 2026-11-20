"""Tests for Pydantic v2 JSON data contracts across all ATRO modules."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from atro.core.schemas import (
    AnalysisResult,
    ClipCandidate,
    DownloadResult,
    JobArtifacts,
    JobRequest,
    JobState,
    JobStatus,
    RenderResult,
    RenderedClip,
    SubtitleCandidate,
    TranscriptResult,
    TranscriptSegment,
)


def test_job_request_bounds_and_validation() -> None:
    req = JobRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert req.clip_count == 4
    assert req.min_duration_sec == 30
    assert req.max_duration_sec == 60
    assert req.aspect_ratio == "9:16"
    assert req.padding_sec == 0.4

    # Invalid URL
    with pytest.raises(ValidationError):
        JobRequest(url="not-a-url")

    # padding_sec below 0.3 or above 0.5
    with pytest.raises(ValidationError):
        JobRequest(url="https://youtu.be/abc", padding_sec=0.25)
    with pytest.raises(ValidationError):
        JobRequest(url="https://youtu.be/abc", padding_sec=0.55)

    # max_duration_sec must exceed min_duration_sec
    with pytest.raises(ValidationError):
        JobRequest(url="https://youtu.be/abc", min_duration_sec=50, max_duration_sec=50)


def test_job_request_local_file_mode() -> None:
    req = JobRequest(
        source_type="local_file",
        local_video_path="D:/videos/podcast.mp4",
    )
    assert req.source_type == "local_file"
    assert req.url is None
    assert req.display_source == "podcast.mp4"

    req_sub = JobRequest(
        source_type="local_file",
        local_video_path="D:/videos/podcast.mkv",
        local_subtitle_path="D:/videos/podcast.srt",
    )
    assert req_sub.local_subtitle_path == "D:/videos/podcast.srt"

    with pytest.raises(ValidationError):
        JobRequest(source_type="local_file", local_video_path="   ")

    with pytest.raises(ValidationError):
        JobRequest(
            source_type="local_file",
            local_video_path="D:/videos/podcast.mp4",
            local_subtitle_path="D:/videos/podcast.txt",
        )


def test_clip_candidate_score_and_span() -> None:
    clip = ClipCandidate(
        title="Hook Kuat",
        start_time=10.0,
        end_time=48.5,
        virality_score=9,
        reason="Hook tajam di 3 detik pertama.",
    )
    assert clip.virality_score == 9

    with pytest.raises(ValidationError):
        ClipCandidate(title="Invalid", start_time=10.0, end_time=10.0, virality_score=8, reason="x")

    with pytest.raises(ValidationError):
        ClipCandidate(title="Score 0", start_time=0.0, end_time=15.0, virality_score=0, reason="x")

    with pytest.raises(ValidationError):
        ClipCandidate(title="Score 11", start_time=0.0, end_time=15.0, virality_score=11, reason="x")


def test_json_contracts_roundtrip() -> None:
    now = datetime.now().astimezone()
    req = JobRequest(url="https://www.youtube.com/watch?v=test123", aspect_ratio="16:9", padding_sec=0.35)
    state = JobState(
        job_id="20261120_141500_a1b2c3",
        status=JobStatus.ANALYZING,
        current_stage="analyzer",
        progress_pct=62.5,
        created_at=now,
        updated_at=now,
        workspace_dir="./workspace/20261120_141500_a1b2c3",
        log_path="./workspace/20261120_141500_a1b2c3/job.log",
        request=req,
        artifacts=JobArtifacts(metadata_json="metadata.json"),
    )
    assert JobState.model_validate_json(state.model_dump_json()).job_id == state.job_id

    dl = DownloadResult(
        job_id=state.job_id,
        url=req.url,
        video_id="test123",
        title="Test Video",
        duration_sec=120.5,
        width=1920,
        height=1080,
        fps=30.0,
        source_video_path="source.mp4",
        subtitle_candidates=[
            SubtitleCandidate(lang="id", is_auto_generated=False, ext="vtt", local_path="source.id.vtt")
        ],
    )
    assert DownloadResult.model_validate_json(dl.model_dump_json()).duration_sec == 120.5

    tr = TranscriptResult(
        job_id=state.job_id,
        source="yt_dlp_manual_sub",
        language="id",
        segments=[TranscriptSegment(index=0, start=0.0, end=4.2, text="Halo semuanya.")],
    )
    assert TranscriptResult.model_validate_json(tr.model_dump_json()).segments[0].text == "Halo semuanya."

    an = AnalysisResult(
        job_id=state.job_id,
        model="gemini-4-neon",
        clips=[
            ClipCandidate(
                title="Klip 1",
                start_time=5.0,
                end_time=40.0,
                virality_score=8,
                reason="Pembuka kontras.",
            )
        ],
    )
    assert AnalysisResult.model_validate_json(an.model_dump_json()).model == "gemini-4-neon"

    rr = RenderResult(
        job_id=state.job_id,
        aspect_ratio="16:9",
        framing_strategy="passthrough_16x9",
        padding_sec=0.35,
        output_dir="./clips",
        rendered_clips=[
            RenderedClip(
                clip_index=1,
                title="Klip 1",
                virality_score=8,
                reason="Pembuka kontras.",
                requested_start_sec=5.0,
                requested_end_sec=40.0,
                padded_start_sec=4.65,
                padded_end_sec=40.35,
                output_duration_sec=35.7,
                output_path="./clips/clip_01_klip_1.mp4",
                file_size_bytes=123456,
            )
        ],
    )
    assert RenderResult.model_validate_json(rr.model_dump_json()).rendered_clips[0].output_duration_sec == 35.7
