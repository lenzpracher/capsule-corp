"""Run a capsule as a Slurm batch job on a cluster.

Compute nodes on most clusters have no outbound network, so the generated script sets
``PI_OFFLINE`` and relies on the capsule's environment already being installed on the
shared filesystem.
"""

from __future__ import annotations

import re
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
    CommandResult,
    Remote,
    mkdir_command,
    rsync_pull_command,
    rsync_push_command,
    run_command,
    ssh_command,
)
from capsule_corp.settings import Settings, SlurmSettings
from capsule_corp.store import CapsuleRef, Catalogue

JOB_ID_PATTERN = re.compile(r"Submitted batch job (\d+)")

# Slurm states that mean the job is no longer running, and whether they count as success.
TERMINAL_STATES = {
    "COMPLETED": True,
    "FAILED": False,
    "CANCELLED": False,
    "TIMEOUT": False,
    "OUT_OF_MEMORY": False,
    "NODE_FAIL": False,
    "PREEMPTED": False,
    "BOOT_FAIL": False,
    "DEADLINE": False,
}


def build_sbatch_script(dirname: str, remote_path: str, slurm: SlurmSettings, spec: ExecutionSpec) -> str:
    """Generate the batch script for one capsule run."""
    directives = [
        f"#SBATCH --job-name=capsule-{dirname}",
        f"#SBATCH --output={remote_path}/results/{STDOUT_LOG}",
        "#SBATCH --error=/dev/null",
        f"#SBATCH --time={spec.time_limit}",
        f"#SBATCH --cpus-per-task={spec.cpus}",
        f"#SBATCH --mem={spec.memory}",
    ]
    if spec.gpus:
        directives.append(f"#SBATCH --gpus={spec.gpus}")
    if slurm.partition:
        directives.append(f"#SBATCH --partition={slurm.partition}")
    if slurm.account:
        directives.append(f"#SBATCH --account={slurm.account}")

    exports = [f"export {key}={value}" for key, value in sorted(spec.env.items())]

    return "\n".join(
        [
            "#!/bin/bash",
            *directives,
            "set -euo pipefail",
            "",
            "# Compute nodes generally have no outbound network.",
            "export PI_OFFLINE=1",
            *exports,
            "",
            f"cd {remote_path}",
            "mkdir -p results",
            "",
            "if [ -f pixi.toml ] && command -v pixi >/dev/null 2>&1; then",
            "  pixi run run",
            "else",
            "  python run.py",
            "fi",
            "",
        ]
    )


class SlurmExecutor:
    """Stages a capsule to a login node, submits an sbatch job, and polls for it."""

    name = "slurm"

    def __init__(self, slurm: SlurmSettings, poll_seconds: int = 20) -> None:
        self.slurm = slurm
        self.poll_seconds = poll_seconds

    def available(self) -> tuple[bool, str]:
        if not self.slurm.host:
            return False, "executors.slurm.host is not set in settings"
        for binary in ("ssh", "rsync"):
            if shutil.which(binary) is None:
                return False, f"{binary} was not found on PATH"
        return True, ""

    @property
    def remote(self) -> Remote:
        if not self.slurm.host:
            raise ExecutorUnavailableError("executors.slurm.host is not set in settings")
        return Remote(host=self.slurm.host, root=self.slurm.remote_root)

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

        self._must(mkdir_command(remote, dirname), "create the remote directory")
        self._must(rsync_push_command(ref.path, remote, dirname), "upload the capsule")

        script = build_sbatch_script(dirname, remote_path, self.slurm, spec)
        # Write the script remotely via a heredoc so no local temp file is needed.
        write = ssh_command(remote, f"cat > {remote_path}/job.sbatch <<'CAPSULE_EOF'\n{script}\nCAPSULE_EOF")
        self._must(write, "write the batch script")

        submitted = self._must(ssh_command(remote, f"sbatch {remote_path}/job.sbatch"), "submit the job")
        match = JOB_ID_PATTERN.search(submitted.output)
        if match is None:
            raise ExecutionError(f"could not parse a job id from sbatch output: {submitted.output.strip()!r}")
        job_id = match.group(1)

        state = self._wait(remote, job_id, spec.timeout_seconds)
        self._pull_results(remote, dirname, ref)

        duration = time.monotonic() - started
        succeeded = TERMINAL_STATES.get(state, False)
        outcome = ExecutionOutcome(
            ok=succeeded,
            exit_code=0 if succeeded else 1,
            duration_seconds=duration,
            stdout_path=ref.results_path / STDOUT_LOG,
            command=["sbatch", f"{remote_path}/job.sbatch"],
            executor=self.name,
            job_id=job_id,
            error=None if succeeded else f"slurm job {job_id} finished in state {state}",
        )
        if outcome.ok:
            _mark_run(catalogue, ref, self.name)
        return outcome

    def _wait(self, remote: Remote, job_id: str, timeout_seconds: int) -> str:
        """Poll sacct until the job leaves the queue. Returns its final state."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = run_command(
                ssh_command(remote, f"sacct -j {job_id} --format=State --noheader --parsable2 | head -1")
            )
            state = result.stdout.strip().split()[0] if result.stdout.strip() else ""
            if state in TERMINAL_STATES:
                return state
            time.sleep(self.poll_seconds)
        run_command(ssh_command(remote, f"scancel {job_id}"))
        return "TIMEOUT"

    def _pull_results(self, remote: Remote, dirname: str, ref: CapsuleRef) -> None:
        ref.results_path.mkdir(parents=True, exist_ok=True)
        run_command(rsync_pull_command(remote, dirname, ref.path))

    def _must(self, argv: list[str], what: str) -> CommandResult:
        result = run_command(argv)
        if not result.ok:
            raise ExecutionError(f"failed to {what}: {result.output.strip() or f'exit {result.exit_code}'}")
        return result
