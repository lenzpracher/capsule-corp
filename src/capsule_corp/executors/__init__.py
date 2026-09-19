"""Where capsules run.

Every backend implements the same :class:`Executor` protocol, so a capsule is written
once and can run on a laptop, a cluster, a container host, or serverless compute
without changing anything inside it.
"""

from __future__ import annotations

from capsule_corp.executors.base import (
    ExecutionError,
    ExecutionOutcome,
    ExecutionSpec,
    Executor,
    ExecutorUnavailableError,
)
from capsule_corp.executors.local import LocalExecutor
from capsule_corp.executors.modal_exec import ModalExecutor
from capsule_corp.executors.slurm import SlurmExecutor
from capsule_corp.executors.ssh_docker import SshDockerExecutor
from capsule_corp.settings import Settings

EXECUTOR_NAMES = ("local", "slurm", "ssh", "modal")


def get_executor(name: str, settings: Settings) -> Executor:
    """Construct the named backend from settings."""
    if name == "local":
        return LocalExecutor()
    if name == "slurm":
        return SlurmExecutor(settings.executors.slurm)
    if name == "ssh":
        return SshDockerExecutor(settings.executors.ssh)
    if name == "modal":
        return ModalExecutor(settings.executors.modal)
    raise ExecutionError(f"unknown executor {name!r}; available: {', '.join(EXECUTOR_NAMES)}")


def spec_from_settings(settings: Settings, executor: str, timeout_seconds: int | None = None) -> ExecutionSpec:
    """Build a resource request, taking cluster-shaped defaults from the Slurm block."""
    slurm = settings.executors.slurm
    return ExecutionSpec(
        cpus=slurm.cpus,
        memory=slurm.mem,
        gpus=slurm.gpus,
        time_limit=slurm.time,
        timeout_seconds=timeout_seconds or settings.agent.timeout_seconds,
    )


__all__ = [
    "EXECUTOR_NAMES",
    "ExecutionError",
    "ExecutionOutcome",
    "ExecutionSpec",
    "Executor",
    "ExecutorUnavailableError",
    "LocalExecutor",
    "ModalExecutor",
    "SlurmExecutor",
    "SshDockerExecutor",
    "get_executor",
    "spec_from_settings",
]
