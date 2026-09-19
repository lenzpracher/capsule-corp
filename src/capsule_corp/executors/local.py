"""Run a capsule on this machine."""

from __future__ import annotations

import shutil
import subprocess
import time

from capsule_corp.executors.base import (
    STDOUT_LOG,
    ExecutionError,
    ExecutionOutcome,
    ExecutionSpec,
)
from capsule_corp.models import CapsuleStatus
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue


def _decode(stream: str | bytes | None) -> str:
    """TimeoutExpired carries bytes even when the call requested text mode."""
    if stream is None:
        return ""
    return stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else stream


def capsule_command(ref: CapsuleRef) -> list[str]:
    """Prefer the capsule's own pixi environment; fall back to bare Python.

    The fallback matters for hand-written or trimmed-down capsules, and for CI, where
    installing a per-capsule environment is often not worth it.
    """
    if (ref.path / "pixi.toml").is_file() and shutil.which("pixi"):
        return ["pixi", "run", "run"]
    return ["python", "run.py"]


class LocalExecutor:
    """Executes `run.py` as a subprocess in the capsule directory."""

    name = "local"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def execute(
        self, catalogue: Catalogue, ref: CapsuleRef, settings: Settings, spec: ExecutionSpec
    ) -> ExecutionOutcome:
        catalogue.verify_frozen(ref)
        if not (ref.path / "run.py").is_file():
            raise ExecutionError(f"capsule {ref.capsule.id} has no run.py; implement it first")

        ref.results_path.mkdir(parents=True, exist_ok=True)
        stdout_path = ref.results_path / STDOUT_LOG
        command = capsule_command(ref)
        started = time.monotonic()

        try:
            completed = subprocess.run(
                command,
                cwd=ref.path,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                check=False,
                env=_merged_env(spec),
            )
            output, exit_code, error = completed.stdout + completed.stderr, completed.returncode, None
        except subprocess.TimeoutExpired as exc:
            output = _decode(exc.stdout) + _decode(exc.stderr) + "\n\n[capsule-corp] timed out"
            exit_code, error = -1, f"execution exceeded {spec.timeout_seconds}s"
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
            executor=self.name,
            error=error,
        )
        if outcome.ok:
            _mark_run(catalogue, ref, self.name)
        return outcome


def _merged_env(spec: ExecutionSpec) -> dict[str, str] | None:
    if not spec.env:
        return None
    import os

    return {**os.environ, **spec.env}


def _mark_run(catalogue: Catalogue, ref: CapsuleRef, executor: str) -> None:
    ref.capsule.provenance.executor = executor
    catalogue.set_status(ref, CapsuleStatus.RUN)
