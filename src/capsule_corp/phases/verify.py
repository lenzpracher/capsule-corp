"""Verification: deterministic checks first, then a blinded judge.

Two independent layers, in that order. The checks are mechanical and decide whether the
registered predictions held. The judge is a language model that reads the question, the
code, and the outputs — and cannot see the write-up — and gives an opinion on whether
the experiment actually supports what it claims.

Only the first layer is a gate by default. The judge is always recorded.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from capsule_corp.checks import CheckResult, evaluate_checks
from capsule_corp.models import (
    CapsuleStatus,
    CheckKind,
    CheckOutcome,
    JudgeVerdict,
    Prereg,
    Verification,
)
from capsule_corp.prompts import JUDGE_PROMPT, JUDGE_SYSTEM
from capsule_corp.runners.base import AgentRequest, Runner
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError

# Exactly what the judge is allowed to see. Anything not listed here is withheld,
# which is what makes the judgement blinded: the author's framing, the write-up, the
# previous verdict, and the git history are all absent by construction rather than by
# an instruction the model could ignore.
# ".prereg.lock" is included deliberately: it carries only a hash, a timestamp and the
# registered check ids -- no authorial claims -- and without it the judge cannot confirm
# for itself that the pre-registration it is reading is the one that was frozen.
BLINDED_ALLOWLIST = ("QUESTION.md", "prereg.toml", ".prereg.lock", "run.py", "pixi.toml", "src", "results")

# Withheld on purpose. Kept explicit so that adding a file to the capsule layout forces
# a decision about whether the judge may see it.
BLINDED_WITHHELD = ("REPORT.md", "verification.json", "capsule.toml", "AGENTS.md", "runs", ".git", ".pi")


class VerificationError(CatalogueError):
    pass


def build_blinded_workspace(ref: CapsuleRef, destination: Path) -> list[str]:
    """Copy only the permitted files into ``destination``. Returns what was copied."""
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in BLINDED_ALLOWLIST:
        source = ref.path / name
        if not source.exists():
            continue
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
        copied.append(name)
    return copied


def parse_verdict(text: str) -> JudgeVerdict:
    """Extract the judge's JSON verdict, tolerating fences and surrounding prose."""
    payload = _extract_json_object(text)
    if payload is None:
        return JudgeVerdict(reasoning="the judge did not return a parseable JSON verdict", raw=text)

    confidence = payload.get("confidence")
    return JudgeVerdict(
        supports_hypothesis=_as_optional_bool(payload.get("supports_hypothesis")),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        reasoning=str(payload.get("reasoning") or ""),
        concerns=[str(c) for c in payload.get("concerns", []) if str(c).strip()],
        raw=None,
    )


def _as_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    # Fall back to the outermost brace-delimited span.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def run_judge(
    ref: CapsuleRef,
    prereg: Prereg,
    runner: Runner,
    settings: Settings,
    workspace: Path,
    events_path: Path | None = None,
) -> JudgeVerdict:
    """Run the blinded judge in a prepared workspace."""
    prompt = JUDGE_PROMPT.format(
        question=ref.capsule.question or ref.capsule.title,
        hypothesis=prereg.hypothesis,
        predictions="\n".join(f"- {p}" for p in prereg.predictions) or "- (none registered)",
    )
    result = runner.run(
        AgentRequest(
            prompt=prompt,
            cwd=workspace,
            system_append=JUDGE_SYSTEM,
            # The judge inspects; it must not be able to alter what it is judging.
            no_builtin_tools=True,
            tools=("read", "list", "glob", "grep"),
            timeout_seconds=settings.agent.timeout_seconds,
        ),
        events_path=events_path,
    )
    verdict = parse_verdict(result.text)
    verdict.provenance = result.provenance
    return verdict


def _status_for(outcomes: list[CheckResult], judge: JudgeVerdict | None, strict: bool) -> CapsuleStatus:
    """Decide the capsule's outcome.

    A capsule that failed to produce its promised artifacts is broken and is marked
    FAILED. A capsule that produced everything but whose predictions did not hold is
    REFUTED -- a completed, informative result, not a failure.
    """
    if any(c.errored for c in outcomes):
        return CapsuleStatus.FAILED
    if any(not c.passed for c in outcomes if c.kind is not CheckKind.EXPR):
        return CapsuleStatus.FAILED
    if any(not c.passed for c in outcomes if c.kind is CheckKind.EXPR):
        return CapsuleStatus.REFUTED
    if strict and judge is not None and judge.supports_hypothesis is False:
        return CapsuleStatus.REFUTED
    return CapsuleStatus.VERIFIED


def verify(
    catalogue: Catalogue,
    ref: CapsuleRef,
    runner: Runner | None,
    settings: Settings,
    *,
    strict: bool = False,
    skip_judge: bool = False,
) -> Verification:
    """Verify a capsule and persist the record to ``verification.json``."""
    catalogue.verify_frozen(ref)

    prereg = catalogue.load_prereg(ref)
    if prereg is None:
        raise VerificationError(f"capsule {ref.capsule.id} has no pre-registration to verify against")

    outcomes = evaluate_checks(prereg, ref.path)

    judge: JudgeVerdict | None = None
    if not skip_judge:
        if runner is None:
            raise VerificationError("a runner is required for the judge; pass skip_judge to run checks only")
        run_dir = catalogue.new_run_dir(ref, phase="judge")
        workspace = run_dir / "blinded"
        build_blinded_workspace(ref, workspace)
        judge = run_judge(ref, prereg, runner, settings, workspace, events_path=run_dir / "events.jsonl")
        catalogue.write_run_meta(run_dir, phase="judge", provenance=judge.provenance, ok=True)
        # The copied workspace is redundant with the capsule itself and can be large.
        shutil.rmtree(workspace, ignore_errors=True)

    report = Verification(
        checks=[
            CheckOutcome(
                id=c.id, kind=c.kind, passed=c.passed, description=c.description, detail=c.detail, error=c.error
            )
            for c in outcomes
        ],
        predictions_held=all(c.passed for c in outcomes if c.kind is CheckKind.EXPR),
        artifacts_complete=all(c.passed for c in outcomes if c.kind is not CheckKind.EXPR),
        judge=judge,
        status=_status_for(outcomes, judge, strict),
    )

    ref.verification_path.write_text(report.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
    catalogue.set_status(ref, report.status)
    return report
