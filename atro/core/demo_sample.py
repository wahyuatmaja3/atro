"""Keyless built-in offline demo podcast project generator for instant ATRO onboarding."""

from __future__ import annotations

import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from atro.core.schemas import (
    AnalysisResult,
    ClipCandidate,
    DownloadResult,
    FocusHintItem,
    JobArtifacts,
    JobRequest,
    JobState,
    JobStatus,
    PlatformPreset,
    RenderedClip,
    RenderResult,
    TopicOutlineItem,
    TranscriptResult,
    TranscriptSegment,
    TranscriptWord,
)
from atro.pipeline.state_store import save_state


def _draw_demo_speaker(
    frame: np.ndarray,
    cx: int,
    cy: int,
    radius: int,
    shirt_bgr: tuple[int, int, int],
    skin_bgr: tuple[int, int, int],
    label: str,
    speaking: bool,
    t_sec: float,
) -> None:
    """Draw a stylized podcast participant head/torso so Studio preview looks crisp."""
    # Shoulders
    cv2.ellipse(frame, (cx, cy + radius + 55), (radius + 26, radius + 14), 0, 180, 360, shirt_bgr, -1)
    # Active speaker halo
    if speaking:
        cv2.circle(frame, (cx, cy), radius + 9, (22, 115, 249), 4, cv2.LINE_AA)
    # Head
    cv2.circle(frame, (cx, cy), radius, skin_bgr, -1, cv2.LINE_AA)
    # Hair arc
    cv2.ellipse(frame, (cx, cy - radius // 4), (radius, int(radius * 0.78)), 0, 180, 360, (28, 26, 24), -1)
    # Eyes
    eye_dx = max(8, radius // 3)
    eye_y = cy - max(4, radius // 8)
    cv2.circle(frame, (cx - eye_dx, eye_y), max(3, radius // 10), (30, 30, 30), -1)
    cv2.circle(frame, (cx + eye_dx, eye_y), max(3, radius // 10), (30, 30, 30), -1)
    # Mouth (animated if speaking)
    mouth_h = 3 if not speaking else int(4 + 5 * abs(np.sin(t_sec * 11.0)))
    cv2.ellipse(frame, (cx, cy + radius // 3), (max(6, radius // 4), mouth_h), 0, 0, 360, (60, 50, 55), -1)
    # Nameplate
    cv2.putText(
        frame,
        label,
        (cx - radius, cy + radius + 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (235, 235, 235),
        2,
        cv2.LINE_AA,
    )


def _write_demo_podcast_video(out_path: Path, duration_sec: float = 16.0, fps: int = 24) -> None:
    """Write a lightweight 1280x720 3-camera / 5-speaker demo podcast MP4."""
    w, h = 1280, 720
    n_frames = max(24, int(round(duration_sec * fps)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, float(fps), (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"Gagal membuat video contoh di {out_path}")

    bg = np.zeros((h, w, 3), dtype=np.uint8)
    bg[:, :] = (24, 22, 20)
    # Studio desk line
    cv2.rectangle(bg, (0, 520), (w, h), (38, 34, 32), -1)
    cv2.line(bg, (0, 520), (w, 520), (22, 115, 249), 3)

    try:
        for idx in range(n_frames):
            t = idx / float(fps)
            frame = bg.copy()
            if t < 6.0:
                # Camera 1: Wide shot of 5 podcast participants
                cam_lbl = "KAMERA 1 (WIDE 5 ORANG)"
                speakers = [
                    (180, 310, 52, (95, 65, 45), (170, 195, 230), "Host", t < 3.0),
                    (410, 305, 50, (45, 85, 110), (165, 190, 225), "Narsum 1", 3.0 <= t < 6.0),
                    (640, 295, 52, (80, 55, 95), (175, 200, 235), "Narsum 2", False),
                    (870, 305, 50, (60, 95, 70), (165, 190, 225), "Panel 4", False),
                    (1100, 310, 52, (110, 70, 50), (160, 185, 220), "Panel 5", False),
                ]
                for cx, cy, r, sb, sk, lbl, spk in speakers:
                    _draw_demo_speaker(frame, cx, cy, r, sb, sk, lbl, spk, t)
            elif t < 11.5:
                # Camera 2: Tight 2-shot Host & Narasumber 2
                cam_lbl = "KAMERA 2 (CLOSE-UP DISKUSI)"
                _draw_demo_speaker(frame, 380, 290, 86, (95, 65, 45), (170, 195, 230), "Host", False, t)
                _draw_demo_speaker(frame, 900, 285, 90, (80, 55, 95), (175, 200, 235), "Narsum 2", True, t)
            else:
                # Camera 3: Reaction Panel 4 & 5
                cam_lbl = "KAMERA 3 (REAKSI PANEL)"
                _draw_demo_speaker(frame, 390, 290, 86, (60, 95, 70), (165, 190, 225), "Panel 4", 11.5 <= t < 14.0, t)
                _draw_demo_speaker(frame, 890, 290, 86, (110, 70, 50), (160, 185, 220), "Panel 5", t >= 14.0, t)

            cv2.putText(frame, cam_lbl, (36, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (200, 200, 200), 2, cv2.LINE_AA)
            writer.write(frame)
    finally:
        writer.release()


def _write_demo_audio_wav(out_path: Path, duration_sec: float = 16.0, sr: int = 16000) -> None:
    """Generate soft modulated synthetic speech-like tone WAV for demo completeness."""
    n = max(sr, int(duration_sec * sr))
    t = np.linspace(0.0, duration_sec, n, endpoint=False)
    env = 0.25 * (0.6 + 0.4 * np.sin(2.0 * np.pi * 2.2 * t))
    sig = env * (0.65 * np.sin(2.0 * np.pi * 165.0 * t) + 0.35 * np.sin(2.0 * np.pi * 330.0 * t))
    pcm = np.clip(sig * 32767.0, -32768, 32767).astype("<i2")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def create_offline_demo_job(
    workspace_dir: Path,
    platform_target: PlatformPreset = "youtube_shorts",
    ffmpeg_path: Optional[str] = None,
) -> JobState:
    """Create a complete offline sample podcast job ready for Studio, Preview & Publish Kit."""
    job_id = f"demo_{uuid.uuid4().hex[:8]}"
    job_dir = Path(workspace_dir) / job_id
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    source_mp4 = job_dir / "source.mp4"
    raw_mp4 = job_dir / "_demo_video.mp4"
    _write_demo_podcast_video(raw_mp4, duration_sec=16.0)
    wav_path = job_dir / "_demo_audio.wav"
    _write_demo_audio_wav(wav_path, duration_sec=16.0)

    # Mux clean playable MP4 if ffmpeg exists, else rename mp4v file directly
    if ffmpeg_path and Path(ffmpeg_path).is_file():
        import subprocess

        cmd = [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(raw_mp4),
            "-i",
            str(wav_path),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source_mp4),
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not source_mp4.is_file():
            raw_mp4.replace(source_mp4)
        else:
            raw_mp4.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
    else:
        raw_mp4.replace(source_mp4)
        wav_path.unlink(missing_ok=True)

    clip1_path = clips_dir / "clip_01_rahasia_hook_podcast.mp4"
    clip2_path = clips_dir / "clip_02_plot_twist_panel.mp4"
    clip1_path.write_bytes(source_mp4.read_bytes())
    clip2_path.write_bytes(source_mp4.read_bytes())

    download = DownloadResult(
        job_id=job_id,
        video_id=job_id,
        title="Contoh Podcast 5 Orang & 3 Kamera (Mode Demo Offline)",
        channel="ATRO Studio Demo",
        duration_sec=16.0,
        width=1280,
        height=720,
        fps=24.0,
        has_audio=True,
        language="id",
        source_video_path=str(source_mp4.resolve()),
    )
    (job_dir / "metadata.json").write_text(download.model_dump_json(indent=2), encoding="utf-8")

    def _words(text: str, start: float, end: float) -> list[TranscriptWord]:
        toks = text.split()
        step = (end - start) / max(1, len(toks))
        return [
            TranscriptWord(
                word=tok,
                start=round(start + i * step, 3),
                end=round(start + (i + 1) * step, 3),
            )
            for i, tok in enumerate(toks)
        ]

    segments = [
        TranscriptSegment(
            index=1,
            start=0.0,
            end=3.2,
            text="Banyak kreator lupa kalau tiga detik pertama menentukan segalanya.",
            words=_words("Banyak kreator lupa kalau tiga detik pertama menentukan segalanya.", 0.0, 3.2),
        ),
        TranscriptSegment(
            index=2,
            start=3.3,
            end=6.2,
            text="Betul banget, apalagi kalau kameranya sering pindah antar pembicara.",
            words=_words("Betul banget, apalagi kalau kameranya sering pindah antar pembicara.", 3.3, 6.2),
        ),
        TranscriptSegment(
            index=3,
            start=6.4,
            end=11.4,
            text="Makanya sistem pintar harus tahu siapa yang ngomong dan kapan reaksi panel disorot.",
            words=_words("Makanya sistem pintar harus tahu siapa yang ngomong dan kapan reaksi panel disorot.", 6.4, 11.4),
        ),
        TranscriptSegment(
            index=4,
            start=11.6,
            end=16.0,
            text="Hasil akhirnya langsung siap upload ke YouTube Shorts dan TikTok!",
            words=_words("Hasil akhirnya langsung siap upload ke YouTube Shorts dan TikTok!", 11.6, 16.0),
        ),
    ]
    transcript = TranscriptResult(
        job_id=job_id,
        source="faster_whisper",
        language="id",
        model_used="demo-offline",
        segments=segments,
    )
    (job_dir / "transcript.json").write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    clips = [
        ClipCandidate(
            title="Tiga Detik Pertama Penentu Viral!",
            start_time=0.0,
            end_time=8.5,
            virality_score=9,
            reason="Hook langsung menyentuh problem utama kreator podcast multi-kamera.",
            focus_hints=[
                FocusHintItem(t_start=0.0, t_end=3.2, role="speaker", target_hint="Host", reason="Hook pembuka"),
                FocusHintItem(t_start=6.4, t_end=8.5, role="speaker", target_hint="Narsum 2", reason="Poin inti"),
            ],
        ),
        ClipCandidate(
            title="Reaksi Panel Saat Kamera Pindah Angle!",
            start_time=8.5,
            end_time=16.0,
            virality_score=8,
            reason="Peralihan cepat antar pembicara dan reaksi studio.",
            focus_hints=[
                FocusHintItem(t_start=11.6, t_end=14.0, role="reaction", target_hint="Panel 4", reason="Reaksi cepat"),
                FocusHintItem(t_start=14.0, t_end=16.0, role="group", target_hint="Panel", reason="Penutup diskusi"),
            ],
        ),
    ]
    topics = [
        TopicOutlineItem(topic="Hook 3 Detik Pertama", start_time=0.0, end_time=6.2, summary="Pentingnya hook & perpindahan kamera"),
        TopicOutlineItem(topic="Fokus Pembicara & Reaksi Panel", start_time=6.4, end_time=16.0, summary="Kamera otomatis mengikuti POI diskusi"),
    ]
    analysis = AnalysisResult(
        job_id=job_id,
        model="demo-offline",
        platform_target=platform_target,
        clips=clips,
        topic_outline=topics,
    )
    (job_dir / "analysis.json").write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

    render_res = RenderResult(
        job_id=job_id,
        aspect_ratio="9:16",
        framing_strategy="smart_face",
        padding_sec=0.4,
        output_dir=str(clips_dir.resolve()),
        rendered_clips=[
            RenderedClip(
                clip_index=1,
                title=clips[0].title,
                virality_score=9,
                reason=clips[0].reason,
                requested_start_sec=0.0,
                requested_end_sec=8.5,
                padded_start_sec=0.0,
                padded_end_sec=8.5,
                output_duration_sec=8.5,
                output_path=str(clip1_path.resolve()),
                file_size_bytes=clip1_path.stat().st_size,
            ),
            RenderedClip(
                clip_index=2,
                title=clips[1].title,
                virality_score=8,
                reason=clips[1].reason,
                requested_start_sec=8.5,
                requested_end_sec=16.0,
                padded_start_sec=8.5,
                padded_end_sec=16.0,
                output_duration_sec=7.5,
                output_path=str(clip2_path.resolve()),
                file_size_bytes=clip2_path.stat().st_size,
            ),
        ],
    )
    (job_dir / "render_result.json").write_text(render_res.model_dump_json(indent=2), encoding="utf-8")

    req = JobRequest(
        source_type="local_file",
        local_video_path=str(source_mp4.resolve()),
        clip_count=2,
        min_duration_sec=5,
        max_duration_sec=60,
        aspect_ratio="9:16",
        edit_format="podcast",
        framing_mode="smart_face",
        platform_target=platform_target,
    )
    now = datetime.now(timezone.utc)
    state = JobState(
        job_id=job_id,
        status=JobStatus.COMPLETED,
        current_stage="completed",
        progress_pct=100.0,
        created_at=now,
        updated_at=now,
        workspace_dir=str(job_dir.resolve()),
        log_path=str((job_dir / "job.log").resolve()),
        request=req,
        artifacts=JobArtifacts(
            metadata_json=str((job_dir / "metadata.json").resolve()),
            transcript_json=str((job_dir / "transcript.json").resolve()),
            analysis_json=str((job_dir / "analysis.json").resolve()),
            render_result_json=str((job_dir / "render_result.json").resolve()),
        ),
    )
    (job_dir / "job.log").write_text("[demo] Project contoh offline siap dibuka di Studio Live Preview.\n", encoding="utf-8")
    save_state(state)
    return state
