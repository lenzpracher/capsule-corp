"""User settings: preferred tooling, agent configuration, and compute backends.

Two layers, both plain TOML so they can be edited by hand or from the TUI settings
screen: a global file under the platform config directory, and an optional
per-catalogue override at ``.capsule-corp/settings.toml``.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import tomlkit
from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, Field

APP_NAME = "capsule-corp"
SETTINGS_FILE = "settings.toml"


class PixiSettings(BaseModel):
    """Defaults stamped into every new capsule's ``pixi.toml``."""

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(default_factory=lambda: ["conda-forge"])
    platforms: list[str] = Field(default_factory=lambda: ["osx-arm64", "linux-64"])
    default_packages: list[str] = Field(default_factory=lambda: ["python=3.12", "numpy", "matplotlib"])


class AgentSettings(BaseModel):
    """How the coding agent is invoked.

    ``provider`` and ``model`` are deliberately None by default: capsule-corp inherits
    whatever ``pi`` is already configured with and records what was actually used,
    rather than pinning a vendor.
    """

    model_config = ConfigDict(extra="forbid")

    runner: str = "pi"
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    # Compute nodes on most clusters have no outbound internet; pi needs telling.
    offline: bool = False
    timeout_seconds: int = 3600


class McpServer(BaseModel):
    """An MCP server made available to capsule runs."""

    model_config = ConfigDict(extra="forbid")

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class SlurmSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    remote_root: str = "~/capsule-corp"
    partition: str | None = None
    account: str | None = None
    time: str = "01:00:00"
    cpus: int = 4
    mem: str = "16G"
    gpus: int = 0


class SshSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str | None = None
    remote_root: str = "~/capsule-corp"
    image: str = "ghcr.io/prefix-dev/pixi:latest"


class ModalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: str = "capsule-corp"
    gpu: str | None = None
    timeout_seconds: int = 3600


class ExecutorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str = "local"
    slurm: SlurmSettings = Field(default_factory=SlurmSettings)
    ssh: SshSettings = Field(default_factory=SshSettings)
    modal: ModalSettings = Field(default_factory=ModalSettings)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pixi: PixiSettings = Field(default_factory=PixiSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    executors: ExecutorSettings = Field(default_factory=ExecutorSettings)
    mcp_servers: list[McpServer] = Field(default_factory=list)

    def enabled_mcp_servers(self) -> list[McpServer]:
        return [s for s in self.mcp_servers if s.enabled]


def global_settings_path() -> Path:
    return Path(user_config_dir(APP_NAME)) / SETTINGS_FILE


def project_settings_path(root: Path) -> Path:
    return root / ".capsule-corp" / SETTINGS_FILE


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return dict(tomlkit.parse(path.read_text(encoding="utf-8")))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base``, returning a new dict.

    Lists are replaced wholesale rather than concatenated, so a project can shrink an
    inherited list instead of only ever growing it.
    """
    merged = deepcopy(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_settings(root: Path | None = None) -> Settings:
    """Global settings, with the catalogue's own settings layered on top."""
    data = _read(global_settings_path())
    if root is not None:
        data = _deep_merge(data, _read(project_settings_path(root)))
    return Settings.model_validate(data)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    """Write settings to ``path``, defaulting to the global location."""
    target = path or global_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomlkit.dumps(settings.model_dump(mode="json", exclude_none=True)), encoding="utf-8")
    return target
