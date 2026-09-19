"""CLI behaviour tests.

These drive the real Typer app so that presentation bugs (wrong exit codes, missing
error messages) are caught alongside library logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from capsule_corp.cli import app

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "."]).exit_code == 0
    return tmp_path


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_list_is_empty_but_not_an_error(workspace: Path) -> None:
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "no capsules yet" in result.stdout


def test_new_then_list_and_show(workspace: Path) -> None:
    assert runner.invoke(app, ["new", "Warmup study", "-q", "Does warmup help?"]).exit_code == 0

    listed = runner.invoke(app, ["list"])
    assert listed.exit_code == 0
    assert "Warmup study" in listed.stdout

    shown = runner.invoke(app, ["show", "0001"])
    assert shown.exit_code == 0
    assert "Does warmup help?" in shown.stdout


def test_folders_and_move(workspace: Path) -> None:
    runner.invoke(app, ["mkdir", "optimization"])
    runner.invoke(app, ["new", "Muon", "--folder", "optimization"])
    assert "optimization" in runner.invoke(app, ["tree"]).stdout

    moved = runner.invoke(app, ["mv", "0001", "archive"])
    assert moved.exit_code == 0
    assert "archive" in runner.invoke(app, ["list"]).stdout


def test_unknown_capsule_exits_nonzero(workspace: Path) -> None:
    result = runner.invoke(app, ["show", "nope"])
    assert result.exit_code != 0


def test_rm_requires_confirmation(workspace: Path) -> None:
    runner.invoke(app, ["new", "Doomed"])
    declined = runner.invoke(app, ["rm", "0001"], input="n\n")
    assert declined.exit_code != 0
    assert runner.invoke(app, ["show", "0001"]).exit_code == 0

    accepted = runner.invoke(app, ["rm", "0001", "--yes"])
    assert accepted.exit_code == 0
    assert runner.invoke(app, ["show", "0001"]).exit_code != 0


def test_freeze_and_reindex_and_search(workspace: Path) -> None:
    runner.invoke(app, ["new", "Warmup", "-q", "Does warmup reduce loss?"])
    prereg = next(workspace.glob("capsules/**/prereg.toml"), None)
    assert prereg is None
    capsule_dir = next(workspace.glob("capsules/0001-*"))
    (capsule_dir / "prereg.toml").write_text(
        'hypothesis = "Warmup reduces loss."\n\n[[checks]]\nid = "c1"\nkind = "expr"\nexpr = "results.a < results.b"\n',
        encoding="utf-8",
    )

    frozen = runner.invoke(app, ["freeze", "0001"])
    assert frozen.exit_code == 0
    assert "frozen" in frozen.stdout

    assert runner.invoke(app, ["reindex"]).exit_code == 0
    found = runner.invoke(app, ["search", "warmup"])
    assert found.exit_code == 0
    assert "0001" in found.stdout


def test_freeze_without_prereg_is_a_clean_error(workspace: Path) -> None:
    runner.invoke(app, ["new", "Nothing"])
    result = runner.invoke(app, ["freeze", "0001"])
    assert result.exit_code != 0


def test_settings_show_reports_inherit(workspace: Path) -> None:
    result = runner.invoke(app, ["settings", "show"])
    assert result.exit_code == 0
    assert "inherit from pi" in result.stdout


def test_help_lists_the_lifecycle_in_running_order() -> None:
    """The order commands are listed in is documentation: design precedes freeze.

    Alphabetical ordering would put `verify` before `design` and obscure the method.
    """
    from capsule_corp.cli import CANONICAL_ORDER

    lifecycle = ["new", "design", "freeze", "implement", "run", "verify"]
    positions = [CANONICAL_ORDER.index(name) for name in lifecycle]
    assert positions == sorted(positions), "the lifecycle must read in the order you run it"
    # unfreeze is the counterpart to freeze, so it belongs beside it rather than
    # wherever it happened to be added.
    assert CANONICAL_ORDER.index("unfreeze") == CANONICAL_ORDER.index("freeze") + 1


def test_every_command_is_ordered() -> None:
    """A new command must be placed deliberately, not appended to the end by accident."""
    from capsule_corp.cli import CANONICAL_ORDER, app

    names: set[str] = set()
    for command in app.registered_commands:
        if command.name:
            names.add(command.name)
        elif command.callback is not None:
            names.add(command.callback.__name__.replace("_", "-"))

    missing = sorted(names - set(CANONICAL_ORDER))
    assert not missing, f"commands missing from CANONICAL_ORDER: {missing}"


def test_help_renders_grouped_panels(workspace: Path) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for panel in ("Lifecycle", "Browsing", "Organising", "Interfaces", "Setup"):
        assert panel in result.stdout
