"""AnalyzerService integrating Gemini (gemini-4-neon) and OpenAI-Compatible LLMs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.schemas import (
    AnalysisResult,
    DownloadResult,
    GeminiClipSelectionSchema,
    JobRequest,
    TranscriptResult,
)
from atro.core.subprocess_utils import JobCancelledError
from atro.modules.analyzer.openai_compat import call_openai_compatible_sync
from atro.modules.analyzer.prompts import build_system_instruction, build_user_prompt
from atro.modules.analyzer.validator import (
    build_heuristic_focus_hints,
    build_heuristic_sfx_cues,
    sanitize_topic_outline,
    validate_and_sanitize_clips,
)
from atro.modules.cutter.sfx import list_all_sfx_names
from atro.modules.transcriber.service import format_transcript_lines


def build_gemini_config(system_instruction: str) -> types.GenerateContentConfig:
    """Construct GenerateContentConfig WITHOUT sampling parameters (temperature, top_p, top_k)."""
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=GeminiClipSelectionSchema,
    )
    # Hard guard: ensure sampling params are never set for gemini-4-neon
    assert config.temperature is None, "temperature tidak boleh dikirim ke gemini-4-neon"
    assert config.top_p is None, "top_p tidak boleh dikirim ke gemini-4-neon"
    assert config.top_k is None, "top_k tidak boleh dikirim ke gemini-4-neon"
    return config


def _call_gemini_sync(
    api_key: str,
    model_name: str,
    user_prompt: str,
    config: types.GenerateContentConfig,
) -> GeminiClipSelectionSchema:
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model_name,
        contents=user_prompt,
        config=config,
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GeminiClipSelectionSchema):
        return parsed
    if isinstance(parsed, dict):
        return GeminiClipSelectionSchema.model_validate(parsed)

    raw_text = getattr(response, "text", None)
    if not raw_text:
        raise RuntimeError("Respons gemini-4-neon kosong dan tidak berisi JSON.")
    return GeminiClipSelectionSchema.model_validate_json(raw_text)


class AnalyzerService:
    """Analyzes timestamped transcripts using Gemini or an OpenAI-Compatible endpoint."""

    async def run(
        self,
        job_id: str,
        download: DownloadResult,
        transcript: TranscriptResult,
        request: JobRequest,
        job_dir: Path,
        settings: AppSettings,
        logger: JobLogger,
        on_progress: Optional[Callable[[float], Awaitable[None] | None]] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AnalysisResult:
        if cancel_event and cancel_event.is_set():
            raise JobCancelledError("Job dibatalkan sebelum analisis AI.")

        if not transcript.segments:
            raise RuntimeError("Transkrip kosong; tidak ada konten untuk dianalisis.")

        available_sfx = list_all_sfx_names()
        formatted_transcript = format_transcript_lines(transcript)
        system_instruction = build_system_instruction(
            request,
            download.duration_sec,
            available_sfx=available_sfx,
        )
        user_prompt = build_user_prompt(download, formatted_transcript)

        if settings.llm_provider == "openai_compatible":
            base_url = (settings.openai_base_url or "").strip()
            model_name = (settings.openai_model or "").strip()
            api_key = settings.resolve_openai_api_key()

            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                raise RuntimeError(
                    "Base URL OpenAI-Compatible tidak valid. Periksa Pengaturan atau OPENAI_BASE_URL."
                )
            if not model_name:
                raise RuntimeError("Nama model OpenAI-Compatible belum dikonfigurasi.")
            if not api_key and not settings.is_openai_local_endpoint():
                raise RuntimeError(
                    "OPENAI_API_KEY belum dikonfigurasi untuk endpoint remote. Tambahkan di Pengaturan atau file .env."
                )

            logger.info(
                "analyzer",
                f"Mengirim {len(transcript.segments)} segmen transkrip ke {model_name} via {base_url}...",
            )
            if on_progress:
                res = on_progress(20.0)
                if asyncio.iscoroutine(res):
                    await res

            raw_selection = await asyncio.to_thread(
                call_openai_compatible_sync,
                base_url,
                api_key,
                model_name,
                system_instruction,
                user_prompt,
            )
            result_model_label = f"openai:{model_name}"
        else:
            api_key = settings.resolve_gemini_api_key()
            if not api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY belum dikonfigurasi. Tambahkan di Pengaturan atau file .env."
                )

            config = build_gemini_config(system_instruction)
            model_name = (settings.gemini_model or "").strip() or "gemini-4-neon"
            logger.info(
                "analyzer",
                f"Mengirim {len(transcript.segments)} segmen transkrip ke {model_name} (Structured Output JSON)...",
            )
            if on_progress:
                res = on_progress(20.0)
                if asyncio.iscoroutine(res):
                    await res

            raw_selection = await asyncio.to_thread(
                _call_gemini_sync,
                api_key,
                model_name,
                user_prompt,
                config,
            )
            result_model_label = model_name

        if cancel_event and cancel_event.is_set():
            raise JobCancelledError("Job dibatalkan setelah respons AI.")

        clips, warnings = validate_and_sanitize_clips(
            raw_selection,
            video_duration_sec=download.duration_sec,
            request=request,
            available_sfx=available_sfx,
        )
        for clip in clips:
            if not clip.focus_hints:
                clip.focus_hints = build_heuristic_focus_hints(
                    transcript.segments,
                    clip.start_time,
                    clip.end_time,
                )
            if not clip.sfx_cues and available_sfx:
                clip.sfx_cues = build_heuristic_sfx_cues(
                    transcript.segments,
                    clip.start_time,
                    clip.end_time,
                    available_sfx=available_sfx,
                )

        for warn in warnings:
            logger.warning("analyzer", warn)

        topic_outline = sanitize_topic_outline(
            raw_selection,
            clips=clips,
            video_duration_sec=download.duration_sec,
        )

        result = AnalysisResult(
            job_id=job_id,
            model=result_model_label,
            platform_target=request.platform_target,
            clips=clips,
            topic_outline=topic_outline,
            validation_warnings=warnings,
        )

        analysis_path = job_dir / "analysis.json"
        analysis_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        if on_progress:
            res = on_progress(100.0)
            if asyncio.iscoroutine(res):
                await res

        logger.info(
            "analyzer",
            f"Kurasi selesai: {len(clips)} klip terpilih oleh {result_model_label}.",
        )
        return result
