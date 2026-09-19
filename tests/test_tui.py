"""Tests for the Textual interface.

Driven through Textual's pilot, so these exercise the real widget tree rather than
calling the helper methods in isolation.
"""

from __future__ import annotations

import json

import pytest
from textual.widgets import RichLog, TextArea, Tree

from capsule_corp.models import CapsuleStatus
from capsule_corp.phases.design import design
from capsule_corp.settings import Settings
from capsule_corp.store import Catalogue
from capsule_corp.tui import CapsuleCorpApp
from capsule_corp.ui import STATUS_GLYPH, STATUS_STYLE, status_glyph, styled_status
from tests.helpers import ScriptedRunner, _writes


def _labels(tree: Tree[object]) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:  # type: ignore[no-untyped-def]
        for child in node.children:
            found.append(str(child.label))
            walk(child)

    walk(tree.root)
    return found


@pytest.mark.anyio
async def test_tree_shows_folders_and_capsules(catalogue: Catalogue) -> None:
    catalogue.create("Warmup", folder="optimization")
    catalogue.create("Tokenizer")

    async with CapsuleCorpApp(catalogue).run_test() as pilot:
        labels = _labels(pilot.app.query_one("#tree", Tree))
        assert any("optimization/" in label for label in labels)
        assert any("Warmup" in label for label in labels)
        assert any("Tokenizer" in label for label in labels)


@pytest.mark.anyio
async def test_empty_catalogue_says_so(catalogue: Catalogue) -> None:
    async with CapsuleCorpApp(catalogue).run_test() as pilot:
        assert any("no capsules yet" in label for label in _labels(pilot.app.query_one("#tree", Tree)))


@pytest.mark.anyio
async def test_detail_pane_renders_prereg_and_verdict(catalogue: Catalogue) -> None:
    ref = catalogue.create("Bernoulli", question="Does it converge?")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    ref.verification_path.write_text(
        json.dumps(
            {
                "checks": [{"id": "slope", "passed": True, "detail": "ok"}],
                "judge": {"supports_hypothesis": False, "reasoning": "Sample too small."},
            }
        ),
        encoding="utf-8",
    )

    app = CapsuleCorpApp(catalogue)
    async with app.run_test():
        rendered = app.render_detail(catalogue.get("0001"))

    assert "Does it converge?" in rendered
    assert "The standard error" in rendered
    assert "slope-near-minus-half" in rendered
    assert "does not support" in rendered
    assert "Sample too small." in rendered


@pytest.mark.anyio
async def test_actions_without_a_selection_are_refused_not_crashed(catalogue: Catalogue) -> None:
    catalogue.create("Something")
    app = CapsuleCorpApp(catalogue)
    async with app.run_test() as pilot:
        await pilot.press("v")
        await pilot.pause()
        log = pilot.app.query_one("#log", RichLog)
        assert log.lines


@pytest.mark.anyio
async def test_freeze_keybinding_advances_status(catalogue: Catalogue) -> None:
    ref = catalogue.create("Freezable")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())

    app = CapsuleCorpApp(catalogue)
    async with app.run_test() as pilot:
        app.selected = "0001"
        await pilot.press("f")
        await pilot.pause()

    assert catalogue.get("0001").capsule.status is CapsuleStatus.FROZEN


@pytest.mark.anyio
async def test_settings_screen_shows_inheritance_and_paths(catalogue: Catalogue) -> None:
    app = CapsuleCorpApp(catalogue)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause()
        text = app.render_settings()

    assert "inherit from pi" in text
    assert "executors.default" in text
    assert "ctrl+r" in text


@pytest.mark.anyio
async def test_reload_picks_up_a_new_capsule(catalogue: Catalogue) -> None:
    app = CapsuleCorpApp(catalogue)
    async with app.run_test() as pilot:
        catalogue.create("Appeared later")
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert any("Appeared later" in label for label in _labels(app.query_one("#tree", Tree)))


def test_every_status_has_a_glyph_and_style() -> None:
    """A new status must not render as a KeyError in the tree."""
    for status in CapsuleStatus:
        assert status in STATUS_GLYPH
        assert status in STATUS_STYLE
        assert status_glyph(status)
        assert styled_status(status)


def test_refuted_and_failed_are_visually_distinct() -> None:
    """A refuted hypothesis is a result, not a breakage; it must not look like one."""
    assert STATUS_STYLE[CapsuleStatus.REFUTED] != STATUS_STYLE[CapsuleStatus.FAILED]
    assert STATUS_GLYPH[CapsuleStatus.REFUTED] != STATUS_GLYPH[CapsuleStatus.FAILED]


@pytest.mark.anyio
async def test_code_screen_opens_and_shows_a_file(catalogue: Catalogue) -> None:
    """Reading the generated code is one keystroke away, not a trip to an editor."""
    from capsule_corp.tui.code import CodeScreen

    ref = catalogue.create("Inspectable")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    (ref.path / "run.py").write_text("import numpy as np\nprint(np.pi)\n", encoding="utf-8")

    app = CapsuleCorpApp(catalogue)
    async with app.run_test() as pilot:
        screen = CodeScreen(ref, frozen=False)
        await app.push_screen(screen)
        await pilot.pause()
        screen.load_file(ref.path / "run.py")
        await pilot.pause()
        editor = screen.query_one("#editor", TextArea)
        assert "import numpy" in editor.text
        assert editor.read_only is False


@pytest.mark.anyio
async def test_code_screen_refuses_to_edit_a_frozen_prereg(catalogue: Catalogue) -> None:
    """You can read the pre-registration you are held to; you cannot quietly edit it."""
    from capsule_corp.tui.code import CodeScreen

    ref = catalogue.create("Locked")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    catalogue.freeze(ref)
    original = ref.prereg_path.read_text(encoding="utf-8")

    app = CapsuleCorpApp(catalogue)
    async with app.run_test() as pilot:
        screen = CodeScreen(ref, frozen=True)
        await app.push_screen(screen)
        await pilot.pause()
        screen.load_file(ref.prereg_path)
        await pilot.pause()

        editor = screen.query_one("#editor", TextArea)
        assert editor.read_only is True, "a frozen pre-registration must not be editable"
        assert "hypothesis" in editor.text, "but it must still be readable"

        editor.read_only = False
        editor.load_text('hypothesis = "something else"\n')
        screen.action_save()
        await pilot.pause()

    assert ref.prereg_path.read_text(encoding="utf-8") == original


@pytest.mark.anyio
async def test_code_screen_saves_an_unfrozen_file(catalogue: Catalogue) -> None:
    from capsule_corp.tui.code import CodeScreen

    ref = catalogue.create("Editable")
    (ref.path / "run.py").write_text("print(1)\n", encoding="utf-8")

    app = CapsuleCorpApp(catalogue)
    async with app.run_test() as pilot:
        screen = CodeScreen(ref, frozen=False)
        await app.push_screen(screen)
        await pilot.pause()
        screen.load_file(ref.path / "run.py")
        await pilot.pause()
        screen.query_one("#editor", TextArea).load_text("print(2)\n")
        screen.action_save()
        await pilot.pause()

    assert (ref.path / "run.py").read_text(encoding="utf-8") == "print(2)\n"


@pytest.mark.anyio
async def test_detail_pane_reports_revisions(catalogue: Catalogue) -> None:
    ref = catalogue.create("Revised")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    catalogue.freeze(ref)
    catalogue.unfreeze(ref, reason="a check was wrong")

    app = CapsuleCorpApp(catalogue)
    async with app.run_test():
        rendered = app.render_detail(catalogue.get("0001"))
    assert "revised" in rendered
