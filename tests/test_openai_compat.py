"""Tests for OpenAI-Compatible analyzer provider, fallback modes, and AppSettings readiness."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atro.config import AppSettings
from atro.core.logging_utils import JobLogger
from atro.core.schemas import DownloadResult, JobRequest, TranscriptResult, TranscriptSegment
from atro.modules.analyzer import openai_compat as compat_mod
from atro.modules.analyzer.openai_compat import (
    build_openai_payload,
    call_openai_compatible_sync,
    extract_json_payload,
    normalize_chat_completions_url,
)
from atro.modules.analyzer.service import AnalyzerService


def test_normalize_chat_completions_url() -> None:
    assert normalize_chat_completions_url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert normalize_chat_completions_url("https://openrouter.ai/api/v1/") == "https://openrouter.ai/api/v1/chat/completions"
    assert (
        normalize_chat_completions_url("http://localhost:11434/v1/chat/completions")
        == "http://localhost:11434/v1/chat/completions"
    )


def test_extract_json_payload_handles_fences_and_surrounding_text() -> None:
    raw_plain = '{"clips": []}'
    assert extract_json_payload(raw_plain) == '{"clips": []}'

    raw_fenced = '```json\n{"clips": [{"title": "A", "start_time": 0, "end_time": 10, "virality_score": 8, "reason": "ok"}]}\n```'
    assert extract_json_payload(raw_fenced).startswith('{"clips":')

    raw_noisy = 'Berikut hasilnya:\n{"clips": []}\nSemoga membantu.'
    assert extract_json_payload(raw_noisy) == '{"clips": []}'


def test_build_openai_payload_modes_and_no_sampling_params() -> None:
    p_schema = build_openai_payload("gpt-4o-mini", "sys", "usr", response_format_mode="json_schema")
    assert p_schema["model"] == "gpt-4o-mini"
    assert p_schema["response_format"]["type"] == "json_schema"
    assert p_schema["response_format"]["json_schema"]["strict"] is True
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in p_schema

    p_obj = build_openai_payload("gpt-4o-mini", "sys", "usr", response_format_mode="json_object")
    assert p_obj["response_format"] == {"type": "json_object"}
    assert "Format JSON WAJIB" in p_obj["messages"][0]["content"]
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in p_obj

    p_none = build_openai_payload("qwen2.5:7b", "sys", "usr", response_format_mode="none")
    assert "response_format" not in p_none
    assert "Format JSON WAJIB" in p_none["messages"][0]["content"]
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in p_none


def test_call_openai_compatible_sync_direct_and_fallbacks(monkeypatch) -> None:
    valid_body = {
        "clips": [
            {
                "title": "Hook Tajam",
                "start_time": 10.0,
                "end_time": 45.0,
                "virality_score": 9,
                "reason": "Pembuka kuat dan klimaks jelas.",
            }
        ]
    }
    ok_response_payload = {"choices": [{"message": {"content": json.dumps(valid_body)}}]}

    # 1. Direct success on json_schema
    seen_modes: list[str] = []

    def fake_post_direct(url, headers=None, json=None, timeout=None):
        seen_modes.append(json.get("response_format", {}).get("type", "none"))
        return SimpleNamespace(status_code=200, json=lambda: ok_response_payload, text="")

    monkeypatch.setattr(compat_mod.requests, "post", fake_post_direct)
    res = call_openai_compatible_sync("https://api.openai.com/v1", "sk-test", "gpt-4o-mini", "sys", "usr")
    assert seen_modes == ["json_schema"]
    assert len(res.clips) == 1

    # 2. Fallback json_schema -> json_object on HTTP 400
    seen_modes.clear()

    def fake_post_fallback_obj(url, headers=None, json=None, timeout=None):
        mode = json.get("response_format", {}).get("type", "none")
        seen_modes.append(mode)
        if mode == "json_schema":
            return SimpleNamespace(status_code=400, text='{"error": "json_schema is not supported"}')
        return SimpleNamespace(status_code=200, json=lambda: ok_response_payload, text="")

    monkeypatch.setattr(compat_mod.requests, "post", fake_post_fallback_obj)
    res_obj = call_openai_compatible_sync("https://api.deepseek.com/v1", "sk-test", "deepseek-chat", "sys", "usr")
    assert seen_modes == ["json_schema", "json_object"]
    assert res_obj.clips[0].title == "Hook Tajam"

    # 3. Fallback json_schema -> json_object -> none on HTTP 400/422
    seen_modes.clear()

    def fake_post_fallback_none(url, headers=None, json=None, timeout=None):
        mode = json.get("response_format", {}).get("type", "none")
        seen_modes.append(mode)
        if mode in ("json_schema", "json_object"):
            return SimpleNamespace(status_code=422, text="response_format unsupported by backend")
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": f"```json\n{json_dumps_body}\n```"}}]},
            text="",
        )

    json_dumps_body = json.dumps(valid_body)
    monkeypatch.setattr(compat_mod.requests, "post", fake_post_fallback_none)
    res_none = call_openai_compatible_sync("http://localhost:11434/v1", "", "qwen2.5:7b", "sys", "usr")
    assert seen_modes == ["json_schema", "json_object", "none"]
    assert res_none.clips[0].virality_score == 9


@pytest.mark.asyncio
async def test_analyzer_service_openai_compatible_end_to_end(tmp_path: Path, monkeypatch) -> None:
    job_dir = tmp_path / "job_openai"
    job_dir.mkdir(parents=True)
    logger = JobLogger("job_openai", job_dir / "job.log")

    settings = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="https://openrouter.ai/api/v1",
        openai_api_key="sk-or-test",
        openai_model="openai/gpt-4o-mini",
        workspace_dir=tmp_path / "ws",
        bin_dir=tmp_path / "bin",
    )

    download = DownloadResult(
        job_id="job_openai",
        url="https://youtu.be/abc",
        video_id="abc",
        title="Podcast Test",
        duration_sec=120.0,
        width=1920,
        height=1080,
        fps=30.0,
        source_video_path=str(job_dir / "source.mp4"),
    )
    transcript = TranscriptResult(
        job_id="job_openai",
        source="faster_whisper",
        language="id",
        segments=[
            TranscriptSegment(index=0, start=0.0, end=12.0, text="Pembuka hook yang sangat kuat."),
            TranscriptSegment(index=1, start=12.0, end=42.0, text="Penjelasan inti dan klimaks cerita."),
        ],
    )
    request = JobRequest(url="https://youtu.be/abc", clip_count=1, min_duration_sec=30, max_duration_sec=60)

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://openrouter.ai/api/v1/chat/completions"
        assert headers["Authorization"] == "Bearer sk-or-test"
        assert headers["X-Title"] == "ATRO"
        payload_out = {
            "choices": [
                {
                    "message": {
                        "content": '{"clips": [{"title": "Momen Emas", "start_time": 0.0, "end_time": 42.0, "virality_score": 8, "reason": "Hook kuat."}]}'
                    }
                }
            ]
        }
        return SimpleNamespace(status_code=200, json=lambda: payload_out, text="")

    monkeypatch.setattr(compat_mod.requests, "post", fake_post)

    result = await AnalyzerService().run(
        job_id="job_openai",
        download=download,
        transcript=transcript,
        request=request,
        job_dir=job_dir,
        settings=settings,
        logger=logger,
    )

    assert result.model == "openai:openai/gpt-4o-mini"
    assert len(result.clips) == 1
    assert result.clips[0].title == "Momen Emas"
    assert (job_dir / "analysis.json").is_file()


def test_app_settings_provider_readiness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    s_gem_empty = AppSettings(llm_provider="gemini", gemini_api_key="", workspace_dir=tmp_path, bin_dir=tmp_path)
    ok, _ = s_gem_empty.is_active_provider_ready()
    assert ok is False

    s_gem_ok = AppSettings(llm_provider="gemini", gemini_api_key="gm-key", workspace_dir=tmp_path, bin_dir=tmp_path)
    ok, _ = s_gem_ok.is_active_provider_ready()
    assert ok is True

    s_oa_remote_no_key = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="",
        openai_model="gpt-4o-mini",
        workspace_dir=tmp_path,
        bin_dir=tmp_path,
    )
    ok, _ = s_oa_remote_no_key.is_active_provider_ready()
    assert ok is False

    s_ollama = AppSettings(
        llm_provider="openai_compatible",
        openai_base_url="http://localhost:11434/v1",
        openai_api_key="",
        openai_model="qwen2.5:7b",
        workspace_dir=tmp_path,
        bin_dir=tmp_path,
    )
    ok, _ = s_ollama.is_active_provider_ready()
    assert ok is True
