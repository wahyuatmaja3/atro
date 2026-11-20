"""Application configuration and runtime path resolution."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from atro.core.schemas import EditFormat, FramingMode

LLMProvider = Literal["gemini", "openai_compatible"]


def get_app_base_dir() -> Path:
    """Return base directory safe for both source execution and PyInstaller frozen exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_BASE_DIR = get_app_base_dir()


class AppSettings(BaseModel):
    """Persistent application settings loaded from .env and config.json."""

    llm_provider: LLMProvider = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-4-neon"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    whisper_model: Literal["tiny", "base", "small", "medium", "large-v3-turbo"] = "small"
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"
    default_clip_count: int = Field(default=4, ge=1, le=10)
    default_min_duration_sec: int = Field(default=30, ge=5, le=300)
    default_max_duration_sec: int = Field(default=60, ge=10, le=600)
    default_aspect_ratio: Literal["16:9", "9:16"] = "9:16"
    default_padding_sec: float = Field(default=0.4, ge=0.3, le=0.5)
    default_edit_format: EditFormat = "podcast"
    default_framing_mode: FramingMode = "smart_face"
    tts_voice: str = "id-ID-GadisNeural"
    tts_rate: str = "+8%"
    workspace_dir: Path = Field(default_factory=lambda: get_app_base_dir() / "workspace")
    bin_dir: Path = Field(default_factory=lambda: get_app_base_dir() / "bin")
    backsound_dir: Path = Field(default_factory=lambda: get_app_base_dir() / "backsound")
    backsound_volume: float = Field(default=0.14, ge=0.0, le=1.0)
    enable_backsound_ducking: bool = True

    @field_validator("workspace_dir", "bin_dir", "backsound_dir", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path | None, info: ValidationInfo) -> Path:
        default_sub = {
            "workspace_dir": "workspace",
            "bin_dir": "bin",
            "backsound_dir": "backsound",
        }.get(info.field_name or "workspace_dir", "workspace")
        if value is None or str(value).strip() == "":
            return get_app_base_dir() / default_sub
        path = Path(value)
        if not path.is_absolute():
            path = (get_app_base_dir() / path).resolve()
        return path

    def resolve_gemini_api_key(self) -> str:
        """Return configured Gemini API key or fallback to GEMINI_API_KEY env var."""
        if self.gemini_api_key and self.gemini_api_key.strip():
            return self.gemini_api_key.strip()
        return os.environ.get("GEMINI_API_KEY", "").strip()

    def resolve_openai_api_key(self) -> str:
        """Return configured OpenAI-compatible API key or fallback to OPENAI_API_KEY env var."""
        if self.openai_api_key and self.openai_api_key.strip():
            return self.openai_api_key.strip()
        return os.environ.get("OPENAI_API_KEY", "").strip()

    def is_openai_local_endpoint(self) -> bool:
        """Return True when openai_base_url targets localhost/loopback (e.g. Ollama, LM Studio)."""
        raw_url = (self.openai_base_url or "").strip()
        if not raw_url:
            return False
        parsed = urlparse(raw_url)
        hostname = (parsed.hostname or "").lower()
        return hostname in {"localhost", "127.0.0.1", "::1"}

    def is_active_provider_ready(self) -> tuple[bool, str]:
        """Validate whether the currently selected LLM provider has required config."""
        if self.llm_provider == "openai_compatible":
            base_url = (self.openai_base_url or "").strip()
            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                return (
                    False,
                    "Base URL OpenAI-Compatible belum valid (harus diawali http:// atau https://).",
                )
            if not (self.openai_model or "").strip():
                return (
                    False,
                    "Model Name OpenAI-Compatible belum diisi. Atur di Pengaturan.",
                )
            if not self.resolve_openai_api_key() and not self.is_openai_local_endpoint():
                return (
                    False,
                    "OPENAI_API_KEY belum diisi untuk endpoint remote. Masukkan API key di Pengaturan atau file .env.",
                )
            return True, ""

        if not (self.gemini_model or "").strip():
            return False, "Nama model Gemini belum diisi. Atur di Pengaturan."
        if not self.resolve_gemini_api_key():
            return (
                False,
                "GEMINI_API_KEY belum diisi. Masukkan API key di Pengaturan atau file .env.",
            )
        return True, ""

    def active_provider_summary(self) -> str:
        """Return compact human-readable label for the active LLM provider and model."""
        if self.llm_provider == "openai_compatible":
            model = (self.openai_model or "").strip() or "-"
            return f"OpenAI-Compat ({model})"
        model = (self.gemini_model or "").strip() or "gemini-4-neon"
        return f"Gemini ({model})"


def get_config_path(base_dir: Optional[Path] = None) -> Path:
    """Return path to persistent config.json."""
    root = base_dir or get_app_base_dir()
    return root / "config.json"


def load_settings(base_dir: Optional[Path] = None) -> AppSettings:
    """Load settings from .env, config.json, and environment variables."""
    root = base_dir or get_app_base_dir()
    dotenv_path = root / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path=dotenv_path, override=False)
    else:
        load_dotenv(override=False)

    raw: dict = {
        "workspace_dir": root / "workspace",
        "bin_dir": root / "bin",
        "backsound_dir": root / "backsound",
    }

    env_ws = os.environ.get("ATRO_WORKSPACE_DIR", "").strip()
    if env_ws:
        raw["workspace_dir"] = env_ws

    env_bgm = os.environ.get("ATRO_BACKSOUND_DIR", "").strip()
    if env_bgm:
        raw["backsound_dir"] = env_bgm

    env_whisper = os.environ.get("ATRO_WHISPER_MODEL", "").strip()
    if env_whisper:
        raw["whisper_model"] = env_whisper

    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        raw["gemini_api_key"] = env_key

    env_provider = os.environ.get("ATRO_LLM_PROVIDER", "").strip()
    if env_provider:
        raw["llm_provider"] = env_provider

    env_openai_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    if env_openai_url:
        raw["openai_base_url"] = env_openai_url

    env_openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_openai_key:
        raw["openai_api_key"] = env_openai_key

    env_openai_model = os.environ.get("OPENAI_MODEL", "").strip()
    if env_openai_model:
        raw["openai_model"] = env_openai_model

    config_path = get_config_path(root)
    if config_path.is_file():
        try:
            file_data = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(file_data, dict):
                # Do not override non-empty env values with empty strings from config.json
                for k, v in file_data.items():
                    if k in {"gemini_api_key", "openai_api_key", "openai_base_url", "openai_model"} and (
                        v is None or str(v).strip() == ""
                    ) and raw.get(k):
                        continue
                    if k in {"workspace_dir", "backsound_dir"} and (v is None or str(v).strip() == ""):
                        continue
                    raw[k] = v
        except Exception:
            # Corrupt config should not crash startup; defaults/env remain usable
            pass

    settings = AppSettings.model_validate(raw)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    settings.backsound_dir.mkdir(parents=True, exist_ok=True)
    return settings


def save_settings(settings: AppSettings, base_dir: Optional[Path] = None) -> Path:
    """Persist settings to config.json atomically."""
    root = base_dir or get_app_base_dir()
    config_path = get_config_path(root)
    payload = settings.model_dump(mode="json")
    payload["workspace_dir"] = str(settings.workspace_dir)
    payload["bin_dir"] = str(settings.bin_dir)
    payload["backsound_dir"] = str(settings.backsound_dir)

    tmp_path = config_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, config_path)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    settings.backsound_dir.mkdir(parents=True, exist_ok=True)
    return config_path
