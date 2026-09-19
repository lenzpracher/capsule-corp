"""The contract between capsule-corp and whichever coding agent writes the code."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from capsule_corp.models import Provenance


class RunnerError(Exception):
    """The agent could not be invoked at all (missing binary, bad configuration)."""


@dataclass(frozen=True)
class Usage:
    """Token and cost accounting for one agent invocation.

    Recorded per capsule so the cost of a research programme is visible rather than
    discovered on a billing page.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class AgentRequest:
    """One agent invocation.

    ``tools`` and ``no_builtin_tools`` exist so the verification judge can be given a
    genuinely read-only agent rather than merely being asked to behave.
    """

    prompt: str
    cwd: Path
    system_append: str | None = None
    tools: tuple[str, ...] | None = None
    no_builtin_tools: bool = False
    session_id: str | None = None
    timeout_seconds: int = 3600


@dataclass
class AgentResult:
    """What an invocation produced, including who produced it."""

    ok: bool
    text: str
    provenance: Provenance
    usage: Usage = field(default_factory=Usage)
    events_path: Path | None = None
    exit_code: int = 0
    error: str | None = None


@runtime_checkable
class Runner(Protocol):
    """A coding agent capsule-corp can drive non-interactively."""

    name: str

    def version(self) -> str | None:
        """Version string of the underlying tool, for provenance."""
        ...

    def run(self, request: AgentRequest, events_path: Path | None = None) -> AgentResult:
        """Execute ``request``, optionally tee-ing the raw event stream to ``events_path``."""
        ...
