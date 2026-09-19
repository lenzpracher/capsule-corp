"""The contract between a capsule and the machine that runs it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError

STDOUT_LOG = "stdout.log"


class ExecutionError(CatalogueError):
    """The capsule could not be executed. Distinct from an experiment that failed."""


class ExecutorUnavailableError(ExecutionError):
    """The backend is not usable here — missing binary, unconfigured host, no auth."""


@dataclass(frozen=True)
class ExecutionSpec:
    """Resources requested for one run.

    Defaults come from settings; a capsule or a single invocation can override them.
    """

    cpus: int = 4
    memory: str = "16G"
    gpus: int = 0
    time_limit: str = "01:00:00"
    timeout_seconds: int = 3600
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionOutcome:
    """What happened when a capsule ran."""

    ok: bool
    exit_code: int
    duration_seconds: float
    stdout_path: Path
    command: list[str]
    executor: str = "local"
    job_id: str | None = None
    error: str | None = None


@runtime_checkable
class Executor(Protocol):
    """Somewhere a capsule can run."""

    name: str

    def available(self) -> tuple[bool, str]:
        """Whether this backend is usable, and why not if it isn't."""
        ...

    def execute(
        self, catalogue: Catalogue, ref: CapsuleRef, settings: Settings, spec: ExecutionSpec
    ) -> ExecutionOutcome:
        """Run the capsule's experiment and leave its outputs in the capsule directory."""
        ...
