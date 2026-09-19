"""End-to-end integration tests against a real coding agent.

These spend money and take minutes, so they are opt-in:

    CAPSULE_CORP_INTEGRATION=1 pixi run pytest tests/test_integration.py -v -s

They exercise the whole lifecycle with whatever model `pi` is configured with, which
is the point: the unit tests use a scripted runner and cannot catch a prompt that a
real model misreads, or a schema a real model declines to follow.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from capsule_corp.executors.base import ExecutionSpec
from capsule_corp.executors.local import LocalExecutor
from capsule_corp.models import CapsuleStatus
from capsule_corp.phases.design import design
from capsule_corp.phases.implement import implement
from capsule_corp.phases.verify import verify
from capsule_corp.runners import PiRunner
from capsule_corp.settings import Settings
from capsule_corp.store import Catalogue, PreregTamperError

ENABLED = os.environ.get("CAPSULE_CORP_INTEGRATION") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ENABLED, reason="set CAPSULE_CORP_INTEGRATION=1 to run (spends money)"),
    pytest.mark.skipif(shutil.which("pi") is None, reason="the pi CLI is not installed"),
]

# Deliberately cheap and fully deterministic: pure numpy, a few seconds of compute, and
# a prediction that is unambiguously either met or not.
QUESTION = (
    "Does the standard error of the sample mean of a Bernoulli(0.3) variable "
    "shrink as 1/sqrt(n)? Use pure numpy, no downloads, and keep it under 30 seconds."
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    # numpy and matplotlib only; a heavier default would make the pixi solve dominate.
    s = Settings()
    s.pixi.default_packages = ["python=3.12", "numpy", "matplotlib"]
    s.agent.timeout_seconds = 900
    return s


def test_full_lifecycle_against_a_real_model(tmp_path_factory, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """design -> freeze -> implement -> run -> verify, with nothing stubbed."""
    root = tmp_path_factory.mktemp("integration")
    catalogue = Catalogue.init(root)
    runner = PiRunner(settings.agent)

    ref = catalogue.create("Bernoulli standard error scaling", question=QUESTION)

    designed = design(catalogue, ref, runner, settings)
    assert designed.prereg.hypothesis
    assert len(designed.prereg.checks) >= 2
    assert designed.prereg.results_contract, "the designer must declare a results contract"
    assert any(c.kind == "expr" for c in designed.prereg.checks)
    print(f"\n[design]    {designed.prereg.hypothesis}")
    print(f"[design]    checks: {[c.id for c in designed.prereg.checks]}")
    print(f"[design]    ${designed.result.usage.cost_usd:.4f} on {designed.result.provenance.model}")

    digest = catalogue.freeze(ref)
    assert len(digest) == 64

    implemented = implement(catalogue, ref, runner, settings)
    assert implemented.result.ok
    assert (ref.path / "run.py").is_file()
    print(f"[implement] ${implemented.result.usage.cost_usd:.4f}")

    outcome = LocalExecutor().execute(catalogue, ref, settings, ExecutionSpec(timeout_seconds=600))
    assert outcome.ok, f"run failed: {outcome.error}\n{outcome.stdout_path.read_text(encoding='utf-8')[-2000:]}"
    assert ref.results_json.is_file(), "the implementation must write results/results.json"

    results = json.loads(ref.results_json.read_text(encoding="utf-8"))
    for key in designed.prereg.results_contract:
        assert key in results, f"the contract promised {key!r} but results.json lacks it"
    print(f"[run]       {outcome.duration_seconds:.1f}s  {results}")

    report = verify(catalogue, ref, runner, settings)
    print(f"[verify]    {report.status}  {sum(c.passed for c in report.checks)}/{len(report.checks)} checks")
    if report.judge is not None:
        print(f"[judge]     supports={report.judge.supports_hypothesis} :: {report.judge.reasoning[:200]}")

    # The hypothesis is true, so a correct implementation should confirm it. A REFUTED
    # outcome here means the experiment was built wrong, not that statistics changed.
    assert report.status is CapsuleStatus.VERIFIED, f"unexpected outcome: {report.status}"
    assert report.predictions_held and report.artifacts_complete
    assert report.judge is not None
    assert ref.verification_path.is_file()


def test_freeze_survives_a_real_implementation_run(tmp_path_factory, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """A real agent must not be able to edit the frozen pre-registration.

    The unit tests prove the check fires; this proves a live model working in the
    capsule directory does not trip it by accident, and that the lock survives an
    agent that was explicitly told to try.
    """
    root = tmp_path_factory.mktemp("tamper")
    catalogue = Catalogue.init(root)
    runner = PiRunner(settings.agent)

    ref = catalogue.create("Tamper resistance", question=QUESTION)
    design(catalogue, ref, runner, settings)
    catalogue.freeze(ref)
    before = catalogue.prereg_digest(ref)

    try:
        implement(catalogue, ref, runner, settings)
    except PreregTamperError:
        pytest.fail("a normal implementation run must not trip the tamper check")

    assert catalogue.prereg_digest(ref) == before
    catalogue.verify_frozen(ref)


def test_judge_is_blinded_in_a_real_run(tmp_path_factory, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """Plant a false claim in REPORT.md and confirm the judge never sees it."""
    root = tmp_path_factory.mktemp("blinding")
    catalogue = Catalogue.init(root)
    runner = PiRunner(settings.agent)

    ref = catalogue.create("Blinding check", question=QUESTION)
    design(catalogue, ref, runner, settings)
    catalogue.freeze(ref)
    implement(catalogue, ref, runner, settings)
    LocalExecutor().execute(catalogue, ref, settings, ExecutionSpec(timeout_seconds=600))

    marker = "ZZQQ-PLANTED-CLAIM-THE-SLOPE-IS-EXACTLY-NEGATIVE-ONE"
    (ref.path / "REPORT.md").write_text(f"# Findings\n\n{marker}\n", encoding="utf-8")

    report = verify(catalogue, ref, runner, settings)
    assert report.judge is not None
    haystack = f"{report.judge.reasoning} {' '.join(report.judge.concerns)}"
    assert marker not in haystack, "the judge saw REPORT.md; blinding is broken"
