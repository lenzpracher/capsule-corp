"""Shared test doubles and fixtures data."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from capsule_corp.models import Provenance
from capsule_corp.runners.base import AgentRequest, AgentResult, Usage

GOOD_PREREG = """\
hypothesis = "The standard error scales as 1/sqrt(n)."
predictions = ["log-log slope is -0.5"]
analysis_plan = "Sample and regress."

[results_contract]
slope = "Log-log slope."
n_seeds = "Seed count."

[[checks]]
id = "slope-near-minus-half"
kind = "expr"
expr = "abs(results.slope + 0.5) < 0.05"

[[checks]]
id = "figure"
kind = "artifact"
path = "results/figures/convergence.png"
"""


class ScriptedRunner:
    """A Runner that performs a supplied side effect instead of calling a model."""

    name = "scripted"

    def __init__(self, effect: Callable[[Path], None] | None = None, *, ok: bool = True) -> None:
        self.effect = effect
        self.ok = ok
        self.requests: list[AgentRequest] = []

    def version(self) -> str | None:
        return "0.0.0-test"

    def run(self, request: AgentRequest, events_path: Path | None = None) -> AgentResult:
        self.requests.append(request)
        if self.effect is not None:
            self.effect(request.cwd)
        if events_path is not None:
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text('{"type":"agent_end"}\n', encoding="utf-8")
        return AgentResult(
            ok=self.ok,
            text="done",
            provenance=Provenance(runner=self.name, provider="test", model="test-model"),
            usage=Usage(total_tokens=100, cost_usd=0.01),
            events_path=events_path,
            error=None if self.ok else "model refused",
        )


def _writes_run_py(cwd: Path) -> None:
    (cwd / "run.py").write_text("print(1)\n", encoding="utf-8")


def _writes(prereg: str = GOOD_PREREG, *, question: bool = True) -> Callable[[Path], None]:
    def effect(cwd: Path) -> None:
        (cwd / "prereg.toml").write_text(prereg, encoding="utf-8")
        if question:
            (cwd / "QUESTION.md").write_text("# Q\n", encoding="utf-8")

    return effect
