"""Run a capsule in a container on any machine reachable over SSH.

The least magical remote backend: rsync the capsule up, run it in a container, rsync
the results back. No vendor account and no scheduler required — a lab workstation is
enough.
"""

from __future__ import annotations

import shutil
import time

from capsule_corp.executors.base import (
    STDOUT_LOG,
    ExecutionError,
    ExecutionOutcome,
    ExecutionSpec,
    ExecutorUnavailableError,
)
from capsule_corp.executors.local import _mark_run
from capsule_corp.executors.transport import (
    Remote,
    mkdir_command,
    rsync_pull_command,
    rsync_push_command,
    run_command,
    ssh_command,
)
from capsule_corp.settings import Settings, SshSettings
from capsule_corp.store import CapsuleRef, Catalogue

CONTAINER_WORKDIR = "/capsule"


def build_docker_command(remote_path: str, image: str, spec: ExecutionSpec) -> str:
    """Build the `docker run` invocation executed on the remote host."""
    parts = [
        "docker run --rm",
        f"-v {remote_path}:{CONTAINER_WORKDIR}",
        f"-w {CONTAINER_WORKDIR}",
        f"--cpus={spec.cpus}",
        f"--memory={spec.memory}",
    ]
    if spec.gpus:
        parts.append(f"--gpus {spec.gpus}")
    for key, value in sorted(spec.env.items()):
        parts.append(f"-e {key}={value}")
    # Containers are generally offline-friendly already, but pi respects this too.
    parts.append("-e PI_OFFLINE=1")
    parts.append(image)
    parts.append(
        "sh -c 'if [ -f pixi.toml ] && command -v pixi >/dev/null 2>&1; then pixi run run; else python run.py; fi'"
    )
    return " ".join(parts)


class SshDockerExecutor:
    """Executes a capsule in a container on a remote host over SSH."""

    name = "ssh"

    def __init__(self, ssh: SshSettings) -> None:
        self.ssh = ssh

    def available(self) -> tuple[bool, str]:
        if not self.ssh.host:
            return False, "executors.ssh.host is not set in settings"
        for binary in ("ssh", "rsync"):
            if shutil.which(binary) is None:
                return False, f"{binary} was not found on PATH"
        return True, ""

    @property
    def remote(self) -> Remote:
        if not self.ssh.host:
            raise ExecutorUnavailableError("executors.ssh.host is not set in settings")
        return Remote(host=self.ssh.host, root=self.ssh.remote_root)

    def execute(
        self, catalogue: Catalogue, ref: CapsuleRef, settings: Settings, spec: ExecutionSpec
    ) -> ExecutionOutcome:
        ok, reason = self.available()
        if not ok:
            raise ExecutorUnavailableError(reason)
        catalogue.verify_frozen(ref)
        if not (ref.path / "run.py").is_file():
            raise ExecutionError(f"capsule {ref.capsule.id} has no run.py; implement it first")

        remote = self.remote
        dirname = ref.capsule.dirname
        remote_path = remote.capsule_path(dirname)
        started = time.monotonic()

        for argv, what in (
            (mkdir_command(remote, dirname), "create the remote directory"),
            (rsync_push_command(ref.path, remote, dirname), "upload the capsule"),
        ):
            result = run_command(argv)
            if not result.ok:
                raise ExecutionError(f"failed to {what}: {result.output.strip() or f'exit {result.exit_code}'}")

        docker = build_docker_command(remote_path, self.ssh.image, spec)
        run = run_command(ssh_command(remote, docker), timeout=spec.timeout_seconds)

        ref.results_path.mkdir(parents=True, exist_ok=True)
        (ref.results_path / STDOUT_LOG).write_text(run.output, encoding="utf-8")
        run_command(rsync_pull_command(remote, dirname, ref.path))

        outcome = ExecutionOutcome(
            ok=run.ok,
            exit_code=run.exit_code,
            duration_seconds=time.monotonic() - started,
            stdout_path=ref.results_path / STDOUT_LOG,
            command=["ssh", remote.host, docker],
            executor=self.name,
            error=None if run.ok else f"remote container exited with status {run.exit_code}",
        )
        if outcome.ok:
            _mark_run(catalogue, ref, self.name)
        return outcome
