"""Shared ssh/rsync plumbing for the remote executors.

The command builders are pure functions so they can be tested without a remote host,
which matters: the Slurm and SSH backends are hard to exercise in CI, so the part that
is easy to get subtly wrong is at least covered.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Never shipped to a remote machine: environments, history, and local caches are either
# huge, machine-specific, or both.
DEFAULT_EXCLUDES = (
    ".pixi/",
    ".git/",
    "__pycache__/",
    "*.pyc",
    "runs/",
    ".capsule-corp/",
)


class TransportError(Exception):
    pass


@dataclass(frozen=True)
class Remote:
    """A host and the directory capsules are staged into on it."""

    host: str
    root: str

    def capsule_path(self, dirname: str) -> str:
        return f"{self.root.rstrip('/')}/{dirname}"


def rsync_push_command(
    local: Path, remote: Remote, dirname: str, excludes: tuple[str, ...] = DEFAULT_EXCLUDES
) -> list[str]:
    """Copy a capsule up to the remote host."""
    argv = ["rsync", "-az", "--delete"]
    for pattern in excludes:
        argv += ["--exclude", pattern]
    # The trailing slash matters: it copies the contents, not the directory itself.
    argv += [f"{local}/", f"{remote.host}:{remote.capsule_path(dirname)}/"]
    return argv


def rsync_pull_command(remote: Remote, dirname: str, local: Path, subpath: str = "results") -> list[str]:
    """Bring a subdirectory of results back down."""
    return [
        "rsync",
        "-az",
        f"{remote.host}:{remote.capsule_path(dirname)}/{subpath}/",
        f"{local / subpath}/",
    ]


def ssh_command(remote: Remote, command: str) -> list[str]:
    """Run a shell command on the remote host."""
    return ["ssh", remote.host, command]


def mkdir_command(remote: Remote, dirname: str) -> list[str]:
    return ssh_command(remote, f"mkdir -p {remote.capsule_path(dirname)}")


@dataclass
class CommandResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def run_command(argv: list[str], timeout: int = 600) -> CommandResult:
    """Run a local command (ssh/rsync included) and capture its output."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TransportError(f"{argv[0]} timed out after {timeout}s") from exc
    except OSError as exc:
        raise TransportError(f"could not run {argv[0]}: {exc}") from exc
    return CommandResult(
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
