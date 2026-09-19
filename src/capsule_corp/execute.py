"""Running a capsule's experiment.

Local execution only for now. The remote backends (Slurm, Modal, SSH+Docker) will
implement the same outcome type behind an ``Executor`` protocol.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from capsule_corp.models import CapsuleStatus
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError

STDOUT_LOG = "stdout.log"


class ExecutionError(CatalogueError):
    pass


@dataclass
class ExecutionOutcome:
    ok: bool
    exit_code: int
    duration_seconds: float
    stdout_path: Path
    command: list[str]
    error: str | None = None


def _decode(stream: str | bytes | None) -> str:
    """TimeoutExpired carries bytes even when the call requested text mode."""
    if stream is None:
        return ""
    return stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else stream


def _command_for(ref: CapsuleRef) -> list[str]:
    """Prefer the capsule's own pixi environment; fall back to bare Python.

    The fallback matters for capsules that were hand-written or trimmed down, and for
    CI, where installing a per-capsule environment is often not worth it.
    """
    if (ref.path / "pixi.toml").is_file() and shutil.which("pixi"):
        return ["pixi", "run", "run"]
    return ["python", "run.py"]


def run_local(
    catalogue: Catalogue,
    ref: CapsuleRef,
    settings: Settings,
    *,
    timeout_seconds: int | None = None,
) -> ExecutionOutcome:
    """Execute the capsule's ``run.py`` and capture its output."""
    catalogue.verify_frozen(ref)

    if not (ref.path / "run.py").is_file():
        raise ExecutionError(f"capsule {ref.capsule.id} has no run.py; implement it first")

    ref.results_path.mkdir(parents=True, exist_ok=True)
    stdout_path = ref.results_path / STDOUT_LOG
    command = _command_for(ref)
    started = time.monotonic()

    try:
        completed = subprocess.run(
            command,
            cwd=ref.path,
            capture_output=True,
            text=True,
            timeout=timeout_seconds or settings.agent.timeout_seconds,
            check=False,
        )
        output, exit_code, error = completed.stdout + completed.stderr, completed.returncode, None
    except subprocess.TimeoutExpired as exc:
        output = _decode(exc.stdout) + _decode(exc.stderr) + "\n\n[capsule-corp] timed out"
        exit_code, error = -1, f"execution exceeded {timeout_seconds or settings.agent.timeout_seconds}s"
    except OSError as exc:
        raise ExecutionError(f"could not run {' '.join(command)}: {exc}") from exc

    duration = time.monotonic() - started
    stdout_path.write_text(output, encoding="utf-8")

    if error is None and exit_code != 0:
        error = f"{' '.join(command)} exited with status {exit_code}"

    outcome = ExecutionOutcome(
        ok=error is None,
        exit_code=exit_code,
        duration_seconds=duration,
        stdout_path=stdout_path,
        command=command,
        error=error,
    )

    if outcome.ok:
        ref.capsule.provenance.executor = "local"
        catalogue.set_status(ref, CapsuleStatus.RUN)

    return outcome
