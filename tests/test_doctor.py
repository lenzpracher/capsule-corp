"""Tests for the environment checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_corp.doctor import Check, _mcp_adapter, _pi_auth, _pi_project_trust, run_checks
from capsule_corp.settings import Settings


def test_run_checks_covers_every_executor() -> None:
    names = {c.name for c in run_checks(Settings())}
    for executor in ("local", "slurm", "ssh", "modal"):
        assert f"executor: {executor}" in names


def test_local_executor_is_always_ready() -> None:
    local = next(c for c in run_checks(Settings()) if c.name == "executor: local")
    assert local.ok


def test_unconfigured_remote_executors_explain_themselves() -> None:
    slurm = next(c for c in run_checks(Settings()) if c.name == "executor: slurm")
    assert not slurm.ok
    assert "host" in slurm.detail


def test_pi_auth_reports_providers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"anthropic": {}, "openai-codex": {}}), encoding="utf-8")
    monkeypatch.setattr("capsule_corp.doctor.PI_AUTH", auth)
    check = _pi_auth()
    assert check.ok
    assert check.detail == "anthropic, openai-codex"


def test_pi_auth_advises_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("capsule_corp.doctor.PI_AUTH", tmp_path / "absent.json")
    check = _pi_auth()
    assert not check.ok
    assert "pi auth" in check.advice


def test_pi_auth_treats_empty_credentials_as_unusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("capsule_corp.doctor.PI_AUTH", auth)
    assert not _pi_auth().ok


def test_project_trust_is_reported_but_never_blocking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The trust setting cannot break us because we pass --approve; say so plainly."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"defaultProjectTrust": "never"}), encoding="utf-8")
    monkeypatch.setattr("capsule_corp.doctor.PI_SETTINGS", settings)
    check = _pi_project_trust()
    assert check.ok
    assert "never" in check.detail
    assert "--approve" in check.detail


def test_mcp_adapter_is_optional() -> None:
    """A missing MCP adapter must not be reported as a blocking failure."""
    check = _mcp_adapter()
    assert isinstance(check, Check)
    if not check.ok and check.advice:
        assert check.advice.startswith("optional") or "npm" in check.advice
