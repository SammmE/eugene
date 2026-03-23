from __future__ import annotations

from pathlib import Path
from typing import Any
import tomllib

from pydantic import BaseModel, ConfigDict, Field, model_validator


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "eugene_data"
APPLETS_DIR = ROOT_DIR / "applets"
CHANNELS_DIR = ROOT_DIR / "channels"
STATIC_DIR = ROOT_DIR / "static"


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
    model_config = ConfigDict(extra="ignore")

    api_key: str
    default_model: str
    router_model: str
    fallback_model: str | None = None
    primary_channel: str | None = None
    max_tool_depth: int = Field(default=5, ge=1, le=20)
    working_memory_turns: int = Field(default=20, ge=4, le=200)
    context_window_threshold: float = Field(default=0.8, gt=0.1, le=1.0)
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    filesystem_root: str = "."
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
    config_path = path or ROOT_DIR / "eugene.toml"
    raw = load_toml(config_path)
    channels = raw.pop("channels", {})
    return EugeneConfig.model_validate({**raw, "channels": channels})


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "applet_configs").mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)
    APPLETS_DIR.mkdir(exist_ok=True)
    CHANNELS_DIR.mkdir(exist_ok=True)
