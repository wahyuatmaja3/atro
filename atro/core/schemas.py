"""Pydantic v2 data contracts shared across all ATRO pipeline modules."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class JobStatus(str, Enum):
    """Execution status of a pipeline job."""

    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    CUTTING = "CUTTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


SourceType = Literal["youtube_url", "local_file"]
AspectRatio = Literal["16:9", "9:16"]
EditFormat = Literal["podcast", "game", "twitch_comment", "subathon"]
FramingMode = Literal["smart_face", "fit_all_blur", "center_crop", "face_tracking"]
TranscriptSource = Literal["yt_dlp_manual_sub", "yt_dlp_auto_sub", "faster_whisper"]
PlatformPreset = Literal["youtube_shorts", "tiktok"]


class JobRequest(BaseModel):
    """Input configuration submitted by user to create an AutoClip job."""

    source_type: SourceType = "youtube_url"
    url: Optional[str] = None
    local_video_path: Optional[str] = None
    local_subtitle_path: Optional[str] = None
    clip_count: int = Field(default=4, ge=1, le=10)
    min_duration_sec: int = Field(default=30, ge=5, le=300)
    max_duration_sec: int = Field(default=60, ge=10, le=600)
    aspect_ratio: AspectRatio = "9:16"
    padding_sec: float = Field(default=0.4, ge=0.3, le=0.5)
    edit_format: EditFormat = "podcast"
    framing_mode: FramingMode = "smart_face"
    platform_target: PlatformPreset = "youtube_shorts"
    excluded_time_spans: list[list[float]] = Field(default_factory=list)
    enable_subtitles: bool = True
    enable_hook_title: bool = True
    enable_opening_tts: bool = True
    enable_backsound: bool = True
    enable_viral_polish: bool = True
    initial_style_overrides: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_request_fields(self) -> "JobRequest":
        if self.source_type == "youtube_url":
            cleaned_url = (self.url or "").strip()
            if not cleaned_url or not (
                cleaned_url.startswith("http://") or cleaned_url.startswith("https://")
            ):
                raise ValueError("URL harus diawali http:// atau https://")
            self.url = cleaned_url
        elif self.source_type == "local_file":
            cleaned_video = (self.local_video_path or "").strip()
            if not cleaned_video:
                raise ValueError("Path video lokal tidak boleh kosong")
            self.local_video_path = cleaned_video

            if self.local_subtitle_path is not None:
                cleaned_sub = self.local_subtitle_path.strip()
                if cleaned_sub:
                    ext = Path(cleaned_sub).suffix.lower()
                    if ext not in {".vtt", ".srt"}:
                        raise ValueError("Subtitle lokal harus berformat .vtt atau .srt")
                    self.local_subtitle_path = cleaned_sub
                else:
                    self.local_subtitle_path = None

        if self.max_duration_sec <= self.min_duration_sec:
            raise ValueError("max_duration_sec harus lebih besar dari min_duration_sec")
        return self

    @property
    def display_source(self) -> str:
        """Human-friendly source label for UI cards and CLI listings."""
        if self.source_type == "local_file" and self.local_video_path:
            return Path(self.local_video_path).name
        return self.url or ""


class JobArtifacts(BaseModel):
    """File paths to JSON contract outputs produced by each module."""

    metadata_json: Optional[str] = None
    transcript_json: Optional[str] = None
    analysis_json: Optional[str] = None
    render_result_json: Optional[str] = None


class JobState(BaseModel):
    """Persistent per-job state saved to ./workspace/<job_id>/state.json."""

    job_id: str
    status: JobStatus
    current_stage: str
    progress_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    created_at: datetime
    updated_at: datetime
    workspace_dir: str
    log_path: str
    request: JobRequest
    artifacts: JobArtifacts = Field(default_factory=JobArtifacts)
    error: Optional[str] = None


class SubtitleCandidate(BaseModel):
    """Subtitle file downloaded alongside source video."""

    lang: str
    is_auto_generated: bool
    ext: str
    local_path: str


class DownloadResult(BaseModel):
    """Output contract of Module 1 (downloader) -> metadata.json."""

    job_id: str
    url: str = ""
    video_id: str
    title: str
    channel: str = ""
    duration_sec: float = Field(gt=0.0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0.0)
    has_audio: bool = True
    language: Optional[str] = None
    source_video_path: str
    subtitle_candidates: list[SubtitleCandidate] = Field(default_factory=list)


class TranscriptWord(BaseModel):
    """Single word with acoustic start/end timestamps in seconds."""

    word: str
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)


class TranscriptSegment(BaseModel):
    """Single timestamped transcript unit."""

    index: int = Field(ge=0)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    text: str
    words: list[TranscriptWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_span(self) -> "TranscriptSegment":
        if self.end <= self.start:
            raise ValueError("TranscriptSegment.end harus lebih besar dari start")
        return self


class TranscriptResult(BaseModel):
    """Output contract of Module 2 (transcriber) -> transcript.json."""

    job_id: str
    source: TranscriptSource
    language: Optional[str] = None
    model_used: Optional[str] = None
    quality_notes: str = ""
    segments: list[TranscriptSegment]


class GeminiFocusHintItem(BaseModel):
    """Gemini Structured Output POI focus hint item (kept flat without exclusiveMinimum)."""

    t_start: float = Field(description="Waktu mulai segmen POI dalam detik absolut transkrip.")
    t_end: float = Field(description="Waktu selesai segmen POI dalam detik absolut transkrip.")
    role: str = Field(default="speaker", description="Jenis sorotan kamera: 'speaker', 'reaction', atau 'group'.")
    target_hint: str = Field(default="", description="Petunjuk pihak yang penting disorot (misal 'host', 'narasumber', 'reaksi pendengar', 'semua').")
    reason: str = Field(default="", description="Alasan singkat titik fokus obrolan di segmen tersebut.")


class GeminiSfxCueItem(BaseModel):
    """Gemini Structured Output contextual meme SFX cue item (flat without exclusiveMinimum)."""

    t_sec: float = Field(description="Waktu muncul SFX dalam detik absolut transkrip (>= 0).")
    sfx_name: str = Field(description="Nama SFX yang dipilih persis dari daftar SFX tersedia.")
    reason: str = Field(default="", description="Alasan singkat momen komedi/reaksi yang cocok.")


class GeminiClipItem(BaseModel):
    """Gemini Structured Output schema item (kept simple without exclusiveMinimum)."""

    title: str = Field(description="Judul singkat dan menarik untuk klip video.")
    start_time: float = Field(description="Waktu mulai klip dalam detik (>= 0).")
    end_time: float = Field(description="Waktu selesai klip dalam detik (> start_time).")
    virality_score: int = Field(description="Skor potensi viral klip dari 1 sampai 10.")
    reason: str = Field(description="Alasan pemilihan klip mencakup kekuatan hook 3 detik pertama dan klimaks.")
    focus_hints: list[GeminiFocusHintItem] = Field(
        default_factory=list,
        description="Opsional: titik penting perbincangan (POI) untuk memandu kamera ke pembicara inti, reaksi, atau group.",
    )
    sfx_cues: list[GeminiSfxCueItem] = Field(
        default_factory=list,
        description="Opsional: momen punchline/reaksi untuk memicu efek suara meme yang sesuai bahasan klip.",
    )


class GeminiTopicOutlineItem(BaseModel):
    """Gemini Structured Output topic/chapter outline item."""

    topic: str = Field(description="Judul topik/segmen pembahasan singkat.")
    start_time: float = Field(description="Waktu mulai topik dalam detik (>= 0).")
    end_time: float = Field(description="Waktu selesai topik dalam detik (> start_time).")
    summary: str = Field(default="", description="Ringkasan 1 kalimat poin bahasan.")


class GeminiClipSelectionSchema(BaseModel):
    """Top-level response_schema passed to gemini-4-neon Structured Output."""

    clips: list[GeminiClipItem] = Field(
        description="Daftar klip terbaik yang berdiri sendiri, tidak tumpang tindih, dan tidak terpotong di tengah kalimat."
    )
    topic_outline: list[GeminiTopicOutlineItem] = Field(
        default_factory=list,
        description="Opsional: daftar topik utama/chapter pembahasan dalam video.",
    )


class FocusHintItem(BaseModel):
    """Validated Point-of-Interest hint for camera focus planning inside a clip."""

    t_start: float = Field(ge=0.0)
    t_end: float = Field(gt=0.0)
    role: Literal["speaker", "reaction", "group"] = "speaker"
    target_hint: str = ""
    reason: str = ""

    @model_validator(mode="after")
    def _validate_span(self) -> "FocusHintItem":
        if self.t_end <= self.t_start:
            self.t_end = round(self.t_start + 1.0, 3)
        return self


class SfxCueItem(BaseModel):
    """Validated contextual SFX trigger inside a clip window."""

    t_sec: float = Field(ge=0.0)
    sfx_name: str
    volume: float = Field(default=0.75, ge=0.0, le=1.0)
    reason: str = ""


class ClipCandidate(BaseModel):
    """Validated clip candidate produced by Module 3 (analyzer)."""

    title: str
    start_time: float = Field(ge=0.0)
    end_time: float = Field(gt=0.0)
    virality_score: int = Field(ge=1, le=10)
    reason: str
    focus_hints: list[FocusHintItem] = Field(default_factory=list)
    sfx_cues: list[SfxCueItem] = Field(default_factory=list)

    @field_validator("title", "reason")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field teks klip tidak boleh kosong")
        return cleaned

    @model_validator(mode="after")
    def _validate_times(self) -> "ClipCandidate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time harus lebih besar dari start_time")
        return self


class TopicOutlineItem(BaseModel):
    """Validated topic/chapter outline segment for video navigation."""

    topic: str
    start_time: float = Field(ge=0.0)
    end_time: float = Field(gt=0.0)
    summary: str = ""

    @model_validator(mode="after")
    def _validate_span(self) -> "TopicOutlineItem":
        if self.end_time <= self.start_time:
            self.end_time = round(self.start_time + 1.0, 3)
        return self


class AnalysisResult(BaseModel):
    """Output contract of Module 3 (analyzer) -> analysis.json."""

    job_id: str
    model: str = "gemini-4-neon"
    platform_target: PlatformPreset = "youtube_shorts"
    clips: list[ClipCandidate]
    topic_outline: list[TopicOutlineItem] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)


class RenderedClip(BaseModel):
    """Single rendered MP4 clip metadata."""

    clip_index: int = Field(ge=1)
    title: str
    virality_score: int = Field(ge=1, le=10)
    reason: str
    requested_start_sec: float = Field(ge=0.0)
    requested_end_sec: float = Field(gt=0.0)
    padded_start_sec: float = Field(ge=0.0)
    padded_end_sec: float = Field(gt=0.0)
    output_duration_sec: float = Field(gt=0.0)
    output_path: str
    file_size_bytes: int = Field(ge=0)
    subtitles_burned: bool = False
    backsound_used: Optional[str] = None
    framing_summary: str = ""
    edit_format: str = "podcast"
    opening_tts_used: bool = False
    intro_duration_sec: float = 0.0
    composer_config_path: Optional[str] = None


class RenderResult(BaseModel):
    """Output contract of Module 4 (cutter) -> render_result.json."""

    job_id: str
    aspect_ratio: AspectRatio
    framing_strategy: str
    padding_sec: float = Field(ge=0.3, le=0.5)
    output_dir: str
    rendered_clips: list[RenderedClip]


class PreflightBinaryInfo(BaseModel):
    """Availability details of a required external binary."""

    name: str
    available: bool
    path: Optional[str] = None
    version: Optional[str] = None
    error: Optional[str] = None


class PreflightReport(BaseModel):
    """Startup dependency verification report."""

    ok: bool
    yt_dlp: PreflightBinaryInfo
    ffmpeg: PreflightBinaryInfo
    ffprobe: PreflightBinaryInfo
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
