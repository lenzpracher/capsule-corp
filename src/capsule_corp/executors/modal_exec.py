"""Run a capsule on Modal's serverless compute.

The path of least setup for someone with no cluster access: no scheduler, no SSH, and
the capsule's conda dependencies rebuilt into the image from its own ``pixi.toml``.

Caveat worth knowing: unlike the SSH and Slurm backends, which speak stable and
widely-available protocols, this one is written against the Modal 1.x Python API and
has not been exercised against a live Modal account. Treat it as provisional.
"""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path
from typing import Any

import tomlkit

from capsule_corp.executors.base import (
    STDOUT_LOG,
    ExecutionError,
    ExecutionOutcome,
    ExecutionSpec,
    ExecutorUnavailableError,
)
from capsule_corp.executors.local import _mark_run
from capsule_corp.settings import ModalSettings, Settings
from capsule_corp.store import CapsuleRef, Catalogue

REMOTE_CAPSULE = "/capsule"


def capsule_packages(ref: CapsuleRef) -> list[str]:
    """Read the capsule's conda dependencies out of its own pixi.toml."""
    manifest = ref.path / "pixi.toml"
    if not manifest.is_file():
        return ["python=3.12"]
    parsed = dict(tomlkit.parse(manifest.read_text(encoding="utf-8")))
    dependencies = parsed.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return ["python=3.12"]
    specs = []
    for name, spec in dependencies.items():
        text = str(spec)
        specs.append(str(name) if text in {"*", ""} else f"{name}{'' if text[0] in '<>=!' else '='}{text}")
    return specs or ["python=3.12"]


def extract_results(payload: bytes, destination: Path) -> list[str]:
    """Unpack the tarball the remote function returned. Returns member names."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = [m for m in archive.getmembers() if _is_safe_member(m.name)]
        # filter="data" is belt and braces alongside _is_safe_member: it also strips
        # ownership, permissions, links and device nodes from an archive we did not build.
        archive.extractall(destination, members=members, filter="data")
    return [m.name for m in members]


def _is_safe_member(name: str) -> bool:
    """Refuse absolute paths and traversal in an archive built off-machine."""
    return not name.startswith("/") and ".." not in Path(name).parts


class ModalExecutor:
    """Runs a capsule as a Modal function and brings its results back as a tarball."""

    name = "modal"

    def __init__(self, modal_settings: ModalSettings) -> None:
        self.modal_settings = modal_settings

    def available(self) -> tuple[bool, str]:
        try:
            import modal  # noqa: F401
        except ImportError:
            return False, "the 'modal' package is not installed; add it to your environment"
        return True, ""

    def execute(
        self, catalogue: Catalogue, ref: CapsuleRef, settings: Settings, spec: ExecutionSpec
    ) -> ExecutionOutcome:
        ok, reason = self.available()
        if not ok:
            raise ExecutorUnavailableError(reason)
        catalogue.verify_frozen(ref)
        if not (ref.path / "run.py").is_file():
            raise ExecutionError(f"capsule {ref.capsule.id} has no run.py; implement it first")

        import modal

        started = time.monotonic()
        image = (
            modal.Image.micromamba()
            .micromamba_install(*capsule_packages(ref), channels=list(settings.pixi.channels))
            .add_local_dir(str(ref.path), remote_path=REMOTE_CAPSULE, copy=True)
        )
        app = modal.App(self.modal_settings.app_name)

        function_kwargs: dict[str, Any] = {
            "image": image,
            "timeout": spec.timeout_seconds or self.modal_settings.timeout_seconds,
        }
        if self.modal_settings.gpu:
            function_kwargs["gpu"] = self.modal_settings.gpu

        remote_run = app.function(**function_kwargs)(_run_capsule_remotely)

        try:
            with app.run():
                payload, exit_code, output = remote_run.remote()
        except Exception as exc:  # Modal surfaces a wide range of failures
            raise ExecutionError(f"modal execution failed: {exc}") from exc

        ref.results_path.mkdir(parents=True, exist_ok=True)
        (ref.results_path / STDOUT_LOG).write_text(output, encoding="utf-8")
        if payload:
            extract_results(payload, ref.path)

        outcome = ExecutionOutcome(
            ok=exit_code == 0,
            exit_code=exit_code,
            duration_seconds=time.monotonic() - started,
            stdout_path=ref.results_path / STDOUT_LOG,
            command=["modal", "run", self.modal_settings.app_name],
            executor=self.name,
            error=None if exit_code == 0 else f"capsule exited with status {exit_code} on Modal",
        )
        if outcome.ok:
            _mark_run(catalogue, ref, self.name)
        return outcome


def _run_capsule_remotely() -> tuple[bytes, int, str]:
    """Execute the capsule inside the Modal container and tar up its results.

    Defined at module scope rather than as a closure so Modal can serialize it.
    """
    import io as _io
    import subprocess as _subprocess
    import tarfile as _tarfile
    from pathlib import Path as _Path

    workdir = _Path(REMOTE_CAPSULE)
    (workdir / "results").mkdir(parents=True, exist_ok=True)

    completed = _subprocess.run(["python", "run.py"], cwd=workdir, capture_output=True, text=True, check=False)
    output = completed.stdout + completed.stderr

    buffer = _io.BytesIO()
    with _tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        results = workdir / "results"
        if results.is_dir():
            archive.add(results, arcname="results")
    return buffer.getvalue(), completed.returncode, output
