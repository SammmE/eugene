from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import tomllib

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "eugene_data"
APPLETS_DIR = ROOT_DIR / "applets"
CHANNELS_DIR = ROOT_DIR / "channels"
STATIC_DIR = ROOT_DIR / "static"


def load_env_file(path: Path | None = None) -> None:
    if load_dotenv is None:
        return
    env_path = path or (ROOT_DIR / ".env")
    if env_path.exists():
        load_dotenv(env_path, override=False)


class ChannelConfig(BaseModel):
    enabled: bool = True
    token: str | None = None
    application_id: str | None = None
    webhook_secret: str | None = None


class ProviderConfig(BaseModel):
    default_model: str
    router_model: str
    fallback_model: str | None = None
    context_window_threshold: float = Field(default=0.8, gt=0.1, le=1.0)


class EugeneConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    api_key: str
    default_model: str
    router_model: str
    fallback_model: str | None = None
    compress_prompt: bool = Field(default=False, alias="compressPrompt")
    compress_prompt_rate: float = Field(default=0.5, gt=0.0, le=1.0, alias="compressPromptRate")
    compress_prompt_model: str = Field(default="microsoft/llmlingua-2-xlm-roberta-large-meetingbank", alias="compressPromptModel")
    compress_prompt_min_chars: int = Field(default=600, ge=1, alias="compressPromptMinChars")
    frontend_auto_reload: bool = Field(default=True, alias="frontendAutoReload")
    frontend_reload_debounce_ms: int = Field(default=600, ge=100, le=5000, alias="frontendReloadDebounceMs")
    primary_channel: str | None = None
    max_tool_depth: int = Field(default=5, ge=1, le=20)
    router_retry_attempts: int = Field(default=2, ge=0, le=10)
    router_error_debug: bool = True
    tool_call_retry_attempts: int = Field(default=2, ge=0, le=10)
    tool_call_error_debug: bool = True
    working_memory_turns: int = Field(default=20, ge=4, le=200)
    context_window_threshold: float = Field(default=0.8, gt=0.1, le=1.0)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    filesystem_root: str = "."
    log_level: str = "INFO"
    log_file: str = "eugene_data/eugene.log"
    log_rotation: str = "10 MB"
    log_retention: str = "14 days"
    log_json: bool = False
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_api_key(self) -> "EugeneConfig":
        if not self.api_key.strip():
            raise ValueError("api_key must not be empty")
        return self

    @property
    def provider(self) -> ProviderConfig:
        return ProviderConfig(
            default_model=self.default_model,
            router_model=self.router_model,
            fallback_model=self.fallback_model,
            context_window_threshold=self.context_window_threshold,
        )


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config(path: Path | None = None) -> EugeneConfig:
    load_env_file()
    config_path = path or ROOT_DIR / "eugene.toml"
    raw = load_toml(config_path)
    channels = raw.pop("channels", {})
    config = EugeneConfig.model_validate({**raw, "channels": channels})

    discord = config.channels.get("discord")
    if discord and not discord.token:
        discord.token = os.getenv("DISCORD_BOT_TOKEN")

    telegram = config.channels.get("telegram")
    if telegram and not telegram.token:
        telegram.token = os.getenv("TELEGRAM_BOT_TOKEN")

    return config


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "applet_configs").mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)
    APPLETS_DIR.mkdir(exist_ok=True)
    CHANNELS_DIR.mkdir(exist_ok=True)
