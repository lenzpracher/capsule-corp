from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from capsule_corp.settings import Settings, _deep_merge, load_settings, project_settings_path, save_settings


def test_defaults_do_not_pin_a_model() -> None:
    """capsule-corp inherits pi's configuration rather than choosing a vendor."""
    settings = Settings()
    assert settings.agent.runner == "pi"
    assert settings.agent.provider is None
    assert settings.agent.model is None


def test_deep_merge_replaces_lists_and_merges_tables() -> None:
    base: dict[str, Any] = {
        "pixi": {"channels": ["conda-forge"], "platforms": ["linux-64"]},
        "agent": {"runner": "pi"},
    }
    override = {"pixi": {"channels": ["my-channel"]}}
    merged = _deep_merge(base, override)
    assert merged["pixi"]["channels"] == ["my-channel"]
    assert merged["pixi"]["platforms"] == ["linux-64"]
    assert merged["agent"]["runner"] == "pi"
    # The original must not be mutated.
    assert base["pixi"]["channels"] == ["conda-forge"]


def test_project_settings_override_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_path = tmp_path / "global" / "settings.toml"
    monkeypatch.setattr("capsule_corp.settings.global_settings_path", lambda: global_path)

    save_settings(Settings(), global_path)
    root = tmp_path / "cat"
    project = project_settings_path(root)
    project.parent.mkdir(parents=True)
    project.write_text('[agent]\nmodel = "pinned-model"\n', encoding="utf-8")

    merged = load_settings(root)
    assert merged.agent.model == "pinned-model"
    assert merged.pixi.channels == ["conda-forge"]


def test_roundtrip(tmp_path: Path) -> None:
    settings = Settings()
    settings.pixi.default_packages = ["python=3.12", "polars"]
    path = save_settings(settings, tmp_path / "settings.toml")
    assert "polars" in path.read_text(encoding="utf-8")
