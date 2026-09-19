"""Design phase: write the question and the pre-registration, before any code exists."""

from __future__ import annotations

from dataclasses import dataclass

from capsule_corp.models import CapsuleStatus, Prereg
from capsule_corp.prompts import DESIGN_PROMPT, DESIGN_SYSTEM
from capsule_corp.runners.base import AgentRequest, AgentResult, Runner
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError, PreregTamperError

MIN_CHECKS = 2
MAX_CHECKS = 8


class DesignError(CatalogueError):
    pass


@dataclass
class DesignOutcome:
    ref: CapsuleRef
    prereg: Prereg
    result: AgentResult


def design(
    catalogue: Catalogue,
    ref: CapsuleRef,
    runner: Runner,
    settings: Settings,
    *,
    extra_instructions: str = "",
) -> DesignOutcome:
    """Run the designer agent and validate what it produced.

    Refuses to run on a frozen capsule: the whole point of freezing is that the
    registered predictions are final.
    """
    if catalogue.is_frozen(ref):
        raise PreregTamperError(
            f"capsule {ref.capsule.id} is already frozen; its design cannot be revisited. "
            "Create a new capsule for a revised question."
        )

    prompt = DESIGN_PROMPT.format(
        title=ref.capsule.title,
        question=ref.capsule.question or ref.capsule.title,
        extra=f"\nADDITIONAL CONSTRAINTS: {extra_instructions}\n" if extra_instructions else "",
        min_checks=MIN_CHECKS,
        max_checks=MAX_CHECKS,
    )

    run_dir = catalogue.new_run_dir(ref, phase="design")
    result = runner.run(
        AgentRequest(
            prompt=prompt,
            cwd=ref.path,
            system_append=DESIGN_SYSTEM,
            timeout_seconds=settings.agent.timeout_seconds,
        ),
        events_path=run_dir / "events.jsonl",
    )
    catalogue.write_run_meta(run_dir, phase="design", provenance=result.provenance, ok=result.ok, error=result.error)

    if not result.ok:
        raise DesignError(f"design phase failed: {result.error}")

    prereg = _validated_prereg(catalogue, ref)

    ref.capsule.status = CapsuleStatus.DESIGNED
    ref.capsule.provenance = result.provenance
    if not ref.capsule.question:
        ref.capsule.question = prereg.hypothesis
    catalogue.save(ref)

    return DesignOutcome(ref=ref, prereg=prereg, result=result)


def _validated_prereg(catalogue: Catalogue, ref: CapsuleRef) -> Prereg:
    """Load the produced pre-registration and reject anything unusable.

    These are the conditions under which freezing would be meaningless, so they are
    caught here rather than surfacing later as a capsule that cannot fail.
    """
    try:
        prereg = catalogue.load_prereg(ref)
    except Exception as exc:  # malformed TOML or schema violation
        raise DesignError(f"the designer produced an unreadable {ref.prereg_path.name}: {exc}") from exc

    if prereg is None:
        raise DesignError(f"the designer did not write {ref.prereg_path.name}")
    if len(prereg.checks) < MIN_CHECKS:
        raise DesignError(f"only {len(prereg.checks)} check(s) registered; at least {MIN_CHECKS} are required")

    declared = set(prereg.results_contract)
    if not declared:
        raise DesignError("the pre-registration declares no [results_contract]; the checks would be unrunnable")

    if not any(check.kind == "expr" for check in prereg.checks):
        raise DesignError("no 'expr' check registered; nothing would mechanically test the hypothesis")

    return prereg
