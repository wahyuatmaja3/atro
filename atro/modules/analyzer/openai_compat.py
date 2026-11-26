"""OpenAI-Compatible Chat Completions client with Structured Output fallback."""

from __future__ import annotations

import re
import threading
from typing import Any, Literal, Optional, TypeVar

import requests
from pydantic import BaseModel

from atro.core.schemas import GeminiClipSelectionSchema
from atro.modules.analyzer.prompts import build_json_schema_reminder

TModel = TypeVar("TModel", bound=BaseModel)

ResponseFormatMode = Literal["json_schema", "json_object", "none"]

_FORMAT_CACHE_LOCK = threading.Lock()
_ENDPOINT_MIN_MODE_IDX: dict[tuple[str, str], int] = {}


class OpenAICompatRequestError(RuntimeError):
    """RuntimeError carrying optional HTTP status code from an OpenAI-Compatible API."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def clear_response_format_cache() -> None:
    """Reset remembered response_format fallback offsets for OpenAI-compatible endpoints."""
    with _FORMAT_CACHE_LOCK:
        _ENDPOINT_MIN_MODE_IDX.clear()

OPENAI_CLIP_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "description": "Daftar klip terbaik yang berdiri sendiri, tidak tumpang tindih, dan tidak terpotong di tengah kalimat.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Judul singkat dan menarik untuk klip video.",
                    },
                    "start_time": {
                        "type": "number",
                        "description": "Waktu mulai klip dalam detik (>= 0).",
                    },
                    "end_time": {
                        "type": "number",
                        "description": "Waktu selesai klip dalam detik (> start_time).",
                    },
                    "virality_score": {
                        "type": "integer",
                        "description": "Skor potensi viral klip dari 1 sampai 10.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Alasan pemilihan klip mencakup kekuatan hook 3 detik pertama dan klimaks.",
                    },
                },
                "required": ["title", "start_time", "end_time", "virality_score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_FORMAT_UNSUPPORTED_HINTS = (
    "response_format",
    "json_schema",
    "structured",
    "not supported",
    "unsupported",
    "invalid",
)


def normalize_chat_completions_url(base_url: str) -> str:
    """Normalize an OpenAI-compatible base URL into a `/chat/completions` endpoint."""
    cleaned = base_url.strip().rstrip("/")
    if not cleaned:
        raise ValueError("Base URL OpenAI-Compatible tidak boleh kosong.")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def extract_json_payload(raw_text: str) -> str:
    """Extract clean JSON object text from raw model output (handling markdown fences)."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = _FENCE_RE.sub("", cleaned).strip()

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return cleaned[first_brace : last_brace + 1]
    return cleaned


def build_openai_payload(
    model_name: str,
    system_instruction: str,
    user_prompt: str,
    response_format_mode: ResponseFormatMode = "json_schema",
    schema_name: str = "clip_selection",
    json_schema: Optional[dict[str, Any]] = None,
    schema_reminder: Optional[str] = None,
) -> dict[str, Any]:
    """Build OpenAI Chat Completions payload without extra sampling overrides."""
    effective_system = system_instruction
    reminder = schema_reminder if schema_reminder is not None else build_json_schema_reminder()
    if response_format_mode == "json_object":
        effective_system = system_instruction + reminder
    elif response_format_mode == "none":
        effective_system = (
            system_instruction
            + reminder
            + "\nPENTING: Jawab HANYA dengan JSON valid sesuai struktur di atas."
        )

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": user_prompt},
        ],
    }

    if response_format_mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": json_schema if json_schema is not None else OPENAI_CLIP_JSON_SCHEMA,
            },
        }
    elif response_format_mode == "json_object":
        payload["response_format"] = {"type": "json_object"}

    return payload


def _extract_message_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if not choices:
        raise RuntimeError("Respons OpenAI-Compatible tidak memiliki field 'choices'.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    raise RuntimeError("Konten respons OpenAI-Compatible kosong atau format tidak dikenali.")


def _should_fallback_response_format(status_code: int, body_text: str) -> bool:
    if status_code not in (400, 422):
        return False
    lowered = body_text.lower()
    return any(hint in lowered for hint in _FORMAT_UNSUPPORTED_HINTS)


def call_openai_structured_json_sync(
    base_url: str,
    api_key: str,
    model_name: str,
    system_instruction: str,
    user_prompt: str,
    schema_model: type[TModel],
    schema_name: str,
    json_schema: dict[str, Any],
    schema_reminder: str,
    timeout: float = 180.0,
) -> TModel:
    """Call an OpenAI-compatible `/chat/completions` API with 3-tier JSON format fallback."""
    endpoint = normalize_chat_completions_url(base_url)
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    if "openrouter.ai" in endpoint.lower():
        headers["X-Title"] = "ATRO"

    modes: tuple[ResponseFormatMode, ...] = ("json_schema", "json_object", "none")
    cache_key = (endpoint.lower(), (model_name or "").strip().lower())
    with _FORMAT_CACHE_LOCK:
        start_idx = _ENDPOINT_MIN_MODE_IDX.get(cache_key, 0)
    last_error_detail = ""
    last_status_code: Optional[int] = None

    for idx in range(start_idx, len(modes)):
        mode = modes[idx]
        payload = build_openai_payload(
            model_name=model_name,
            system_instruction=system_instruction,
            user_prompt=user_prompt,
            response_format_mode=mode,
            schema_name=schema_name,
            json_schema=json_schema,
            schema_reminder=schema_reminder,
        )
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            raise OpenAICompatRequestError(
                f"Gagal menghubungi endpoint '{endpoint}': {exc}",
                status_code=None,
            ) from exc

        if resp.status_code < 400:
            if idx > start_idx:
                with _FORMAT_CACHE_LOCK:
                    _ENDPOINT_MIN_MODE_IDX[cache_key] = max(
                        _ENDPOINT_MIN_MODE_IDX.get(cache_key, 0), idx
                    )
            try:
                data = resp.json()
            except ValueError as exc:
                raise RuntimeError(f"Respons endpoint bukan JSON valid: {resp.text[:300]}") from exc
            raw_text = _extract_message_text(data)
            cleaned_json = extract_json_payload(raw_text)
            if not cleaned_json:
                raise RuntimeError("Respons OpenAI-Compatible kosong dan tidak berisi JSON.")
            try:
                return schema_model.model_validate_json(cleaned_json)
            except Exception as exc:
                raise RuntimeError(
                    f"Output model tidak sesuai schema JSON: {exc}. Cuplikan: {cleaned_json[:300]}"
                ) from exc

        last_status_code = resp.status_code
        snippet = (resp.text or "").strip()[:400]
        last_error_detail = f"HTTP {resp.status_code}: {snippet}"
        has_next = idx < len(modes) - 1
        if has_next and _should_fallback_response_format(resp.status_code, resp.text or ""):
            continue
        raise OpenAICompatRequestError(
            f"Error dari endpoint OpenAI-Compatible ({last_error_detail})",
            status_code=last_status_code,
        )

    raise OpenAICompatRequestError(
        f"Error dari endpoint OpenAI-Compatible ({last_error_detail})",
        status_code=last_status_code,
    )


def call_openai_compatible_sync(
    base_url: str,
    api_key: str,
    model_name: str,
    system_instruction: str,
    user_prompt: str,
    timeout: float = 180.0,
) -> GeminiClipSelectionSchema:
    """Call an OpenAI-compatible `/chat/completions` API for viral clip curation."""
    return call_openai_structured_json_sync(
        base_url=base_url,
        api_key=api_key,
        model_name=model_name,
        system_instruction=system_instruction,
        user_prompt=user_prompt,
        schema_model=GeminiClipSelectionSchema,
        schema_name="clip_selection",
        json_schema=OPENAI_CLIP_JSON_SCHEMA,
        schema_reminder=build_json_schema_reminder(),
        timeout=timeout,
    )
