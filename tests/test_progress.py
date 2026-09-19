"""Tests for the live phase progress reporter."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console

from capsule_corp.progress import PhaseReporter, _describe, _shorten


def _reporter(quiet: bool = False) -> tuple[PhaseReporter, Console]:
    console = Console(force_terminal=False, width=100, record=True)
    return PhaseReporter(console, "designing", quiet=quiet), console


def test_tool_calls_are_logged_as_they_happen() -> None:
    """The whole point: the user can see what the agent is doing to their capsule."""
    reporter, console = _reporter()
    reporter.handle({"type": "tool_execution_start", "toolName": "write", "args": {"path": "prereg.toml"}})
    reporter.handle({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "pixi run run"}})
    output = console.export_text()
    assert "wrote" in output
    assert "prereg.toml" in output
    assert "ran" in output
    assert "pixi run run" in output


def test_tool_errors_are_surfaced() -> None:
    reporter, console = _reporter()
    reporter.handle({"type": "tool_execution_end", "isError": True, "result": "permission denied"})
    assert "permission denied" in console.export_text()


def test_successful_tool_end_is_not_noise() -> None:
    reporter, console = _reporter()
    reporter.handle({"type": "tool_execution_end", "isError": False, "result": "ok"})
    assert console.export_text().strip() == ""


def test_token_count_accumulates() -> None:
    reporter, _ = _reporter()
    reporter.handle({"type": "message_update", "usage": {"totalTokens": 120}, "assistantMessageEvent": {}})
    reporter.handle({"type": "message_update", "usage": {"totalTokens": 480}, "assistantMessageEvent": {}})
    assert "480" in reporter._summary()


def test_assistant_text_is_captured_so_questions_are_not_silent() -> None:
    """pi cannot pause to ask in non-interactive mode, so a question arrives as text."""
    reporter, console = _reporter()
    for delta in ("Should I ", "use 50 seeds ", "or 100?"):
        reporter.handle({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": delta}})
    assert reporter.text == "Should I use 50 seeds or 100?"
    reporter.final_note()
    assert "50 seeds" in console.export_text()


def test_text_end_replaces_accumulated_deltas() -> None:
    reporter, _ = _reporter()
    reporter.handle({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "par"}})
    reporter.handle(
        {"type": "message_update", "assistantMessageEvent": {"type": "text_end", "content": "partial then final"}}
    )
    assert reporter.text == "partial then final"


def test_quiet_suppresses_output() -> None:
    reporter, console = _reporter(quiet=True)
    reporter.handle({"type": "tool_execution_start", "toolName": "write", "args": {"path": "x"}})
    reporter.final_note()
    assert console.export_text().strip() == ""


def test_unknown_events_are_ignored() -> None:
    reporter, console = _reporter()
    events: list[dict[str, Any]] = [
        {"type": "agent_start"},
        {"type": "turn_end"},
        {},
        {"type": "session", "id": "x"},
    ]
    for event in events:
        reporter.handle(event)
    assert console.export_text().strip() == ""


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"path": "results/x.json"}, "results/x.json"),
        ({"command": "python run.py"}, "python run.py"),
        ({"pattern": "*.py"}, "*.py"),
        ({"unrelated": 1}, ""),
        (None, ""),
    ],
)
def test_describe_picks_the_meaningful_argument(args: Any, expected: str) -> None:
    assert _describe("read", args) == expected


def test_shorten_truncates_and_collapses_whitespace() -> None:
    assert _shorten("a   b\n c") == "a b c"
    assert len(_shorten("x" * 200)) <= 72
