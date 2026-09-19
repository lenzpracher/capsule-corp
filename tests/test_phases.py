"""Tests for the agent phases, using a scripted runner instead of a real model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_corp.models import CapsuleStatus
from capsule_corp.phases import scaffold_capsule
from capsule_corp.phases.design import DesignError, design
from capsule_corp.phases.implement import ImplementError, implement
from capsule_corp.phases.scaffold import _split_spec
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue, PreregTamperError
from tests.helpers import GOOD_PREREG, ScriptedRunner, _writes, _writes_run_py


@pytest.fixture
def ref(catalogue: Catalogue) -> CapsuleRef:
    return catalogue.create("Bernoulli convergence", question="Does it converge at 1/sqrt(n)?")


@pytest.fixture
def settings() -> Settings:
    return Settings()


# ----------------------------------------------------------------------- design


def test_design_writes_and_validates_prereg(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    outcome = design(catalogue, ref, ScriptedRunner(_writes()), settings)
    assert outcome.prereg.hypothesis.startswith("The standard error")
    assert len(outcome.prereg.checks) == 2
    assert catalogue.get(ref.capsule.id).capsule.status is CapsuleStatus.DESIGNED


def test_design_records_provenance(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    design(catalogue, ref, ScriptedRunner(_writes()), settings)
    saved = catalogue.get(ref.capsule.id).capsule
    assert saved.provenance.model == "test-model"


def test_design_writes_a_run_record(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    design(catalogue, ref, ScriptedRunner(_writes()), settings)
    metas = list(ref.runs_path.glob("*-design/meta.json"))
    assert len(metas) == 1
    meta = json.loads(metas[0].read_text(encoding="utf-8"))
    assert meta["phase"] == "design" and meta["ok"] is True


def test_design_rejects_missing_prereg(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    with pytest.raises(DesignError, match="did not write"):
        design(catalogue, ref, ScriptedRunner(lambda cwd: None), settings)


def test_design_rejects_too_few_checks(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    one_check = GOOD_PREREG.split("[[checks]]")[0] + '[[checks]]\nid = "only"\nkind = "expr"\nexpr = "True"\n'
    with pytest.raises(DesignError, match="at least 2 are required"):
        design(catalogue, ref, ScriptedRunner(_writes(one_check)), settings)


def test_design_rejects_missing_results_contract(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    """Checks reference results.* keys; without a contract they could never be run."""
    no_contract = GOOD_PREREG.replace('[results_contract]\nslope = "Log-log slope."\nn_seeds = "Seed count."\n', "")
    with pytest.raises(DesignError, match="no \\[results_contract\\]"):
        design(catalogue, ref, ScriptedRunner(_writes(no_contract)), settings)


def test_design_rejects_prereg_with_no_expr_check(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    artifact_only = GOOD_PREREG.replace(
        '[[checks]]\nid = "slope-near-minus-half"\nkind = "expr"\nexpr = "abs(results.slope + 0.5) < 0.05"\n',
        '[[checks]]\nid = "other-figure"\nkind = "artifact"\npath = "results/figures/b.png"\n',
    )
    with pytest.raises(DesignError, match="nothing would mechanically test"):
        design(catalogue, ref, ScriptedRunner(_writes(artifact_only)), settings)


def test_design_rejects_unreadable_prereg(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    with pytest.raises(DesignError, match="unreadable"):
        design(catalogue, ref, ScriptedRunner(_writes("this is not toml {{{")), settings)


def test_design_refuses_on_a_frozen_capsule(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    design(catalogue, ref, ScriptedRunner(_writes()), settings)
    catalogue.freeze(ref)
    with pytest.raises(PreregTamperError, match="already frozen"):
        design(catalogue, ref, ScriptedRunner(_writes()), settings)


def test_design_surfaces_runner_failure(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    with pytest.raises(DesignError, match="model refused"):
        design(catalogue, ref, ScriptedRunner(_writes(), ok=False), settings)


# ------------------------------------------------------------------- implement


def _designed_and_frozen(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    design(catalogue, ref, ScriptedRunner(_writes()), settings)
    catalogue.freeze(ref)


def test_implement_succeeds_and_advances_status(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    _designed_and_frozen(catalogue, ref, settings)
    implement(catalogue, ref, ScriptedRunner(_writes_run_py), settings)
    assert catalogue.get(ref.capsule.id).capsule.status is CapsuleStatus.IMPLEMENTED


def test_implement_requires_freeze(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    design(catalogue, ref, ScriptedRunner(_writes()), settings)
    with pytest.raises(Exception, match="not frozen"):
        implement(catalogue, ref, ScriptedRunner(), settings)


def test_implement_detects_a_rewritten_prereg(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    """The guarantee the whole design exists to provide.

    An agent that cannot satisfy the registered checks must not be able to relax them.
    """
    _designed_and_frozen(catalogue, ref, settings)

    def cheat(cwd: Path) -> None:
        (cwd / "run.py").write_text("print(1)\n", encoding="utf-8")
        prereg = cwd / "prereg.toml"
        prereg.write_text(
            prereg.read_text(encoding="utf-8").replace("abs(results.slope + 0.5) < 0.05", "True"),
            encoding="utf-8",
        )

    with pytest.raises(PreregTamperError, match="changed after freezing"):
        implement(catalogue, ref, ScriptedRunner(cheat), settings)


def test_tamper_is_caught_even_when_the_agent_failed(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    """A failed run can still have modified files, so the lock is always re-checked."""
    _designed_and_frozen(catalogue, ref, settings)

    def cheat(cwd: Path) -> None:
        (cwd / "prereg.toml").write_text('hypothesis = "anything"\n', encoding="utf-8")

    with pytest.raises(PreregTamperError):
        implement(catalogue, ref, ScriptedRunner(cheat, ok=False), settings)


def test_implement_requires_run_py(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    _designed_and_frozen(catalogue, ref, settings)
    with pytest.raises(ImplementError, match="did not produce run.py"):
        implement(catalogue, ref, ScriptedRunner(), settings)


def test_implement_prompt_carries_the_results_contract(
    catalogue: Catalogue, ref: CapsuleRef, settings: Settings
) -> None:
    _designed_and_frozen(catalogue, ref, settings)
    runner = ScriptedRunner(_writes_run_py)
    implement(catalogue, ref, runner, settings)
    assert "slope" in runner.requests[0].prompt
    assert "Log-log slope." in runner.requests[0].prompt


# -------------------------------------------------------------------- scaffold


def test_scaffold_writes_capsule_config(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    written = scaffold_capsule(ref, settings)
    assert set(written) == {"pixi.toml", ".pi/settings.json", "AGENTS.md"}
    assert (ref.path / "pixi.toml").is_file()
    assert (ref.path / ".pi" / "settings.json").is_file()


def test_scaffold_does_not_clobber(catalogue: Catalogue, ref: CapsuleRef, settings: Settings) -> None:
    scaffold_capsule(ref, settings)
    (ref.path / "pixi.toml").write_text("# hand-edited\n", encoding="utf-8")
    assert scaffold_capsule(ref, settings) == []
    assert "hand-edited" in (ref.path / "pixi.toml").read_text(encoding="utf-8")


def test_scaffold_pi_settings_empty_when_nothing_pinned(
    catalogue: Catalogue, ref: CapsuleRef, settings: Settings
) -> None:
    scaffold_capsule(ref, settings)
    assert json.loads((ref.path / ".pi" / "settings.json").read_text(encoding="utf-8")) == {}


def test_scaffold_pi_settings_pins_when_configured(catalogue: Catalogue, ref: CapsuleRef) -> None:
    settings = Settings()
    settings.agent.provider = "anthropic"
    settings.agent.model = "claude-opus-5"
    scaffold_capsule(ref, settings)
    pinned = json.loads((ref.path / ".pi" / "settings.json").read_text(encoding="utf-8"))
    assert pinned == {"defaultProvider": "anthropic", "defaultModel": "claude-opus-5"}


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("numpy", ("numpy", "*")),
        ("python=3.12", ("python", "3.12")),
        ("numpy>=2.3", ("numpy", ">=2.3")),
        ("torch==2.4.0", ("torch", "==2.4.0")),
    ],
)
def test_split_spec(spec: str, expected: tuple[str, str]) -> None:
    assert _split_spec(spec) == expected
