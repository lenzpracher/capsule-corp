"""Tests for the pi runner: command construction and event-stream parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from capsule_corp.runners.base import AgentRequest, RunnerError
from capsule_corp.runners.pi import PiRunner, _EventCollector, _parse_usage, get_runner
from capsule_corp.settings import AgentSettings


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> PiRunner:
    monkeypatch.setattr("capsule_corp.runners.pi.shutil.which", lambda _: "/usr/local/bin/pi")
    return PiRunner(AgentSettings())


def _request(tmp_path: Path, **kwargs: object) -> AgentRequest:
    return AgentRequest(prompt="do the thing", cwd=tmp_path, **kwargs)  # type: ignore[arg-type]


def test_argv_always_passes_approve(runner: PiRunner, tmp_path: Path) -> None:
    """Regression guard for pi's project-trust behaviour.

    pi never prompts for trust in non-interactive modes, and with the default
    ``defaultProjectTrust = "ask"`` it silently ignores the capsule's .pi/settings.json.
    Dropping --approve would make per-capsule agent config quietly stop working.
    """
    argv = runner.build_argv(_request(tmp_path))
    assert "--approve" in argv


def test_argv_is_non_interactive_json(runner: PiRunner, tmp_path: Path) -> None:
    argv = runner.build_argv(_request(tmp_path))
    assert "-p" in argv
    assert argv[argv.index("--mode") + 1] == "json"
    assert argv[-1] == "do the thing"


def test_argv_omits_provider_and_model_by_default(runner: PiRunner, tmp_path: Path) -> None:
    """Unpinned settings must inherit pi's own configuration rather than forcing one."""
    argv = runner.build_argv(_request(tmp_path))
    assert "--provider" not in argv
    assert "--model" not in argv


def test_argv_pins_when_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("capsule_corp.runners.pi.shutil.which", lambda _: "/usr/local/bin/pi")
    pinned = PiRunner(AgentSettings(provider="anthropic", model="claude-opus-5", thinking="high", offline=True))
    argv = pinned.build_argv(_request(tmp_path))
    assert argv[argv.index("--provider") + 1] == "anthropic"
    assert argv[argv.index("--model") + 1] == "claude-opus-5"
    assert argv[argv.index("--thinking") + 1] == "high"
    assert "--offline" in argv


def test_argv_restricts_tools_for_a_read_only_agent(runner: PiRunner, tmp_path: Path) -> None:
    argv = runner.build_argv(_request(tmp_path, no_builtin_tools=True, tools=("read",)))
    assert "--no-builtin-tools" in argv
    assert argv[argv.index("--tools") + 1] == "read"


def test_missing_binary_is_a_clean_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("capsule_corp.runners.pi.shutil.which", lambda _: None)
    with pytest.raises(RunnerError, match="was not found on PATH"):
        PiRunner(AgentSettings()).build_argv(_request(tmp_path))


def test_unknown_runner_rejected() -> None:
    with pytest.raises(RunnerError, match="unknown runner"):
        get_runner(AgentSettings(runner="nonesuch"))


# Recorded from a real `pi -p --mode json` invocation (pi 0.85.1).
SESSION = {"type": "session", "version": 3, "id": "01a0ba58", "cwd": "/tmp/x"}
ASSISTANT_END = {
    "type": "message_end",
    "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "PONG"}],
        "api": "openai-codex-responses",
        "provider": "openai-codex",
        "model": "gpt-5.6-terra",
        "usage": {
            "input": 406,
            "output": 6,
            "totalTokens": 412,
            "cost": {"input": 0.001, "output": 0.0002, "total": 0.0012},
        },
        "stopReason": "stop",
    },
}


def _collect(*events: Mapping[str, Any]) -> _EventCollector:
    collector = _EventCollector()
    for event in events:
        collector.feed(json.dumps(event) + "\n")
    return collector


def test_collector_extracts_provenance_and_text() -> None:
    collector = _collect(SESSION, ASSISTANT_END)
    assert collector.session_id == "01a0ba58"
    assert collector.provider == "openai-codex"
    assert collector.model == "gpt-5.6-terra"
    assert collector.text == "PONG"
    assert collector.usage.total_tokens == 412
    assert collector.usage.cost_usd == pytest.approx(0.0012)


def test_collector_ignores_user_messages_and_deltas() -> None:
    user_end = {"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}
    delta = {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "P"}}
    collector = _collect(SESSION, user_end, delta, ASSISTANT_END)
    assert collector.text == "PONG"


def test_collector_survives_non_json_noise() -> None:
    collector = _EventCollector()
    collector.feed("warning: something from npm\n")
    collector.feed(json.dumps(ASSISTANT_END) + "\n")
    assert collector.text == "PONG"


def test_collector_reports_model_errors() -> None:
    errored = {
        "type": "message_end",
        "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "rate limited"},
    }
    assert _collect(SESSION, errored).error_message == "rate limited"


def test_parse_usage_tolerates_missing_cost() -> None:
    usage = _parse_usage({"input": 1, "output": 2, "totalTokens": 3})
    assert usage is not None
    assert usage.cost_usd == 0.0
    assert _parse_usage(None) is None
