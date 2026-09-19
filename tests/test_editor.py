"""Tests for external editor resolution."""

from __future__ import annotations

import pytest

from capsule_corp.editor import EditorError, is_terminal_editor, resolve_editor
from capsule_corp.settings import EditorSettings


def test_explicit_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("capsule_corp.editor.shutil.which", lambda name: f"/usr/bin/{name}")
    assert resolve_editor(EditorSettings(), "code") == "code"


def test_settings_command_beats_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("capsule_corp.editor.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("EDITOR", "vim")
    assert resolve_editor(EditorSettings(command="cursor")) == "cursor"


def test_environment_used_when_nothing_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("capsule_corp.editor.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "nano")
    assert resolve_editor(EditorSettings()) == "nano"


def test_falls_back_to_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("capsule_corp.editor.shutil.which", lambda name: "/usr/bin/zed" if name == "zed" else None)
    assert resolve_editor(EditorSettings()) == "zed"


def test_missing_configured_editor_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("capsule_corp.editor.shutil.which", lambda name: None)
    with pytest.raises(EditorError, match="not found on PATH"):
        resolve_editor(EditorSettings(command="nonesuch"))


def test_no_editor_anywhere_explains_how_to_fix_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("capsule_corp.editor.shutil.which", lambda name: None)
    with pytest.raises(EditorError, match=r"Set \$EDITOR"):
        resolve_editor(EditorSettings())


@pytest.mark.parametrize(
    ("command", "expected"),
    [("vim", True), ("nvim", True), ("nano", True), ("code", False), ("code --wait", False), ("cursor", False)],
)
def test_terminal_editors_are_recognised(command: str, expected: bool) -> None:
    """A blocking editor launched from the TUI would deadlock the interface."""
    assert is_terminal_editor(command) is expected
