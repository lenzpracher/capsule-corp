"""Implementation phase: write the code, under a frozen pre-registration."""

from __future__ import annotations

from dataclasses import dataclass

from capsule_corp.models import CapsuleStatus
from capsule_corp.phases.scaffold import scaffold_capsule
from capsule_corp.prompts import IMPLEMENT_PROMPT, IMPLEMENT_SYSTEM, format_contract
from capsule_corp.runners.base import AgentRequest, AgentResult, Runner
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError


class ImplementError(CatalogueError):
    pass


@dataclass
class ImplementOutcome:
    ref: CapsuleRef
    result: AgentResult


def implement(
    catalogue: Catalogue,
    ref: CapsuleRef,
    runner: Runner,
    settings: Settings,
) -> ImplementOutcome:
    """Run the implementing agent between two pre-registration hash checks.

    The check after the run is the one that matters: it is what stops an agent from
    rewriting the predictions it could not satisfy.
    """
    catalogue.verify_frozen(ref)

    prereg = catalogue.load_prereg(ref)
    if prereg is None:
        raise ImplementError(f"capsule {ref.capsule.id} has no pre-registration to implement")

    scaffold_capsule(ref, settings)

    prompt = IMPLEMENT_PROMPT.format(
        hypothesis=prereg.hypothesis,
        contract=format_contract(prereg.results_contract),
    )

    run_dir = catalogue.new_run_dir(ref, phase="implement")
    result = runner.run(
        AgentRequest(
            prompt=prompt,
            cwd=ref.path,
            system_append=IMPLEMENT_SYSTEM,
            timeout_seconds=settings.agent.timeout_seconds,
        ),
        events_path=run_dir / "events.jsonl",
    )
    catalogue.write_run_meta(run_dir, phase="implement", provenance=result.provenance, ok=result.ok, error=result.error)

    # Always re-check the lock, even when the agent reported failure: a failed run can
    # still have modified files.
    catalogue.verify_frozen(ref)

    if not result.ok:
        raise ImplementError(f"implementation phase failed: {result.error}")
    if not (ref.path / "run.py").is_file():
        raise ImplementError("the implementation did not produce run.py")

    ref.capsule.status = CapsuleStatus.IMPLEMENTED
    ref.capsule.provenance = result.provenance
    catalogue.save(ref)

    return ImplementOutcome(ref=ref, result=result)
