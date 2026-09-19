"""Tests for the verification phase: blinding, verdict parsing, and outcome rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_corp.models import CapsuleStatus, Provenance
from capsule_corp.phases.design import design
from capsule_corp.phases.verify import (
    BLINDED_WITHHELD,
    build_blinded_workspace,
    parse_verdict,
    verify,
)
from capsule_corp.runners.base import AgentRequest, AgentResult, Usage
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue
from tests.helpers import GOOD_PREREG, ScriptedRunner, _writes

GOOD_RESULTS = {"slope": -0.503, "n_seeds": 50}


class JudgeRunner:
    """A runner that returns a fixed judge reply and remembers what it could see."""

    name = "judge-stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.saw: list[str] = []

    def version(self) -> str | None:
        return "0.0.0-test"

    def run(self, request: AgentRequest, events_path: Path | None = None) -> AgentResult:
        self.saw = sorted(p.name for p in request.cwd.iterdir())
        self.request = request
        if events_path is not None:
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text("{}\n", encoding="utf-8")
        return AgentResult(
            ok=True,
            text=self.reply,
            provenance=Provenance(runner=self.name, provider="test", model="judge-model"),
            usage=Usage(),
        )


@pytest.fixture
def ready(catalogue: Catalogue) -> CapsuleRef:
    """A frozen, implemented capsule with passing results."""
    ref = catalogue.create("Bernoulli", question="Does it converge?")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    catalogue.freeze(ref)
    (ref.path / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (ref.results_path / "figures").mkdir(parents=True, exist_ok=True)
    (ref.results_path / "figures" / "convergence.png").write_bytes(b"\x89PNG")
    ref.results_json.write_text(json.dumps(GOOD_RESULTS), encoding="utf-8")
    return ref


# --------------------------------------------------------------------- blinding


def test_blinded_workspace_excludes_the_write_up(catalogue: Catalogue, ready: CapsuleRef, tmp_path: Path) -> None:
    """The judge must not be able to read the author's conclusions."""
    (ready.path / "REPORT.md").write_text("We conclusively proved the hypothesis!\n", encoding="utf-8")
    (ready.path / "verification.json").write_text("{}", encoding="utf-8")

    workspace = tmp_path / "blinded"
    build_blinded_workspace(ready, workspace)

    present = {p.name for p in workspace.iterdir()}
    assert "QUESTION.md" in present
    assert "prereg.toml" in present
    assert "results" in present
    # The judge must be able to confirm the pre-registration it reads is the frozen one.
    assert ".prereg.lock" in present
    for withheld in BLINDED_WITHHELD:
        assert withheld not in present, f"{withheld} leaked into the blinded workspace"


def test_judge_only_ever_sees_permitted_files(catalogue: Catalogue, ready: CapsuleRef) -> None:
    (ready.path / "REPORT.md").write_text("The hypothesis holds.\n", encoding="utf-8")
    judge = JudgeRunner('{"supports_hypothesis": true, "confidence": 0.9, "reasoning": "ok", "concerns": []}')
    verify(catalogue, ready, judge, Settings())
    assert "REPORT.md" not in judge.saw
    assert "capsule.toml" not in judge.saw
    assert "QUESTION.md" in judge.saw


def test_judge_runs_read_only(catalogue: Catalogue, ready: CapsuleRef) -> None:
    """Blinding is worthless if the judge can edit what it is judging."""
    judge = JudgeRunner('{"supports_hypothesis": true}')
    verify(catalogue, ready, judge, Settings())
    assert judge.request.no_builtin_tools is True
    assert judge.request.tools is not None
    assert "write" not in judge.request.tools
    assert "edit" not in judge.request.tools


# -------------------------------------------------------------- verdict parsing


@pytest.mark.parametrize(
    "text",
    [
        '{"supports_hypothesis": true, "confidence": 0.8, "reasoning": "Clear.", "concerns": []}',
        'Here is my verdict:\n```json\n{"supports_hypothesis": true, "confidence": 0.8, "reasoning": "Clear."}\n```',
        'Sure.\n{"supports_hypothesis": true, "confidence": 0.8, "reasoning": "Clear."}\nHope that helps.',
    ],
)
def test_parse_verdict_tolerates_wrapping(text: str) -> None:
    verdict = parse_verdict(text)
    assert verdict.supports_hypothesis is True
    assert verdict.confidence == pytest.approx(0.8)


def test_parse_verdict_handles_refusal_to_decide() -> None:
    verdict = parse_verdict('{"supports_hypothesis": null, "reasoning": "Not enough seeds to tell."}')
    assert verdict.supports_hypothesis is None
    assert "seeds" in verdict.reasoning


def test_parse_verdict_keeps_unparseable_text() -> None:
    verdict = parse_verdict("I could not determine this.")
    assert verdict.supports_hypothesis is None
    assert verdict.raw == "I could not determine this."


def test_parse_verdict_collects_concerns() -> None:
    verdict = parse_verdict('{"supports_hypothesis": false, "concerns": ["only 3 seeds", "tolerance is suspicious"]}')
    assert verdict.concerns == ["only 3 seeds", "tolerance is suspicious"]


# ---------------------------------------------------------------------- outcomes


def test_passing_capsule_is_verified(catalogue: Catalogue, ready: CapsuleRef) -> None:
    judge = JudgeRunner('{"supports_hypothesis": true, "confidence": 0.9}')
    report = verify(catalogue, ready, judge, Settings())
    assert report.status is CapsuleStatus.VERIFIED
    assert report.predictions_held and report.artifacts_complete
    assert catalogue.get(ready.capsule.id).capsule.status is CapsuleStatus.VERIFIED


def test_failed_prediction_is_refuted_not_failed(catalogue: Catalogue, ready: CapsuleRef) -> None:
    """A hypothesis that does not hold is a completed result, not a broken capsule."""
    ready.results_json.write_text(json.dumps({"slope": -0.9, "n_seeds": 50}), encoding="utf-8")
    report = verify(catalogue, ready, None, Settings(), skip_judge=True)
    assert report.status is CapsuleStatus.REFUTED
    assert not report.predictions_held
    assert report.artifacts_complete


def test_missing_artifact_is_a_failure(catalogue: Catalogue, ready: CapsuleRef) -> None:
    """A capsule that did not produce what it promised is broken, not informative."""
    (ready.results_path / "figures" / "convergence.png").unlink()
    report = verify(catalogue, ready, None, Settings(), skip_judge=True)
    assert report.status is CapsuleStatus.FAILED
    assert not report.artifacts_complete


def test_unevaluable_check_is_a_failure(catalogue: Catalogue, ready: CapsuleRef) -> None:
    ready.results_json.write_text(json.dumps({"wrong_key": 1}), encoding="utf-8")
    report = verify(catalogue, ready, None, Settings(), skip_judge=True)
    assert report.status is CapsuleStatus.FAILED


def test_judge_disagreement_is_recorded_but_not_blocking(catalogue: Catalogue, ready: CapsuleRef) -> None:
    judge = JudgeRunner('{"supports_hypothesis": false, "confidence": 0.7, "reasoning": "Sample too small."}')
    report = verify(catalogue, ready, judge, Settings())
    assert report.status is CapsuleStatus.VERIFIED
    assert report.judge is not None
    assert report.judge.supports_hypothesis is False


def test_strict_mode_lets_the_judge_block(catalogue: Catalogue, ready: CapsuleRef) -> None:
    judge = JudgeRunner('{"supports_hypothesis": false, "confidence": 0.7, "reasoning": "Sample too small."}')
    report = verify(catalogue, ready, judge, Settings(), strict=True)
    assert report.status is CapsuleStatus.REFUTED


def test_verification_is_persisted(catalogue: Catalogue, ready: CapsuleRef) -> None:
    verify(catalogue, ready, None, Settings(), skip_judge=True)
    saved = json.loads(ready.verification_path.read_text(encoding="utf-8"))
    assert saved["status"] == "verified"
    assert len(saved["checks"]) == 2


def test_verify_requires_a_frozen_prereg(catalogue: Catalogue) -> None:
    ref = catalogue.create("Unfrozen")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    with pytest.raises(Exception, match="not frozen"):
        verify(catalogue, ref, None, Settings(), skip_judge=True)


def test_verify_detects_tampering(catalogue: Catalogue, ready: CapsuleRef) -> None:
    ready.prereg_path.write_text(GOOD_PREREG.replace("0.05", "99.0"), encoding="utf-8")
    with pytest.raises(Exception, match="changed after freezing"):
        verify(catalogue, ready, None, Settings(), skip_judge=True)


def test_judge_required_unless_skipped(catalogue: Catalogue, ready: CapsuleRef) -> None:
    with pytest.raises(Exception, match="runner is required"):
        verify(catalogue, ready, None, Settings())
