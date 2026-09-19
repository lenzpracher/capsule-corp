"""Tests for the execution backends.

The remote backends cannot be exercised without a cluster or a container host, so the
command construction — the part that is easy to get subtly and silently wrong — is
tested directly, and the local backend is tested end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capsule_corp.executors import EXECUTOR_NAMES, ExecutionError, get_executor, spec_from_settings
from capsule_corp.executors.base import ExecutionSpec
from capsule_corp.executors.local import LocalExecutor, capsule_command
from capsule_corp.executors.modal_exec import capsule_packages, extract_results
from capsule_corp.executors.slurm import JOB_ID_PATTERN, SlurmExecutor, build_sbatch_script
from capsule_corp.executors.ssh_docker import SshDockerExecutor, build_docker_command
from capsule_corp.executors.transport import (
    Remote,
    mkdir_command,
    rsync_pull_command,
    rsync_push_command,
    ssh_command,
)
from capsule_corp.models import CapsuleStatus
from capsule_corp.phases.design import design
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue
from tests.helpers import ScriptedRunner, _writes

REMOTE = Remote(host="sherlock", root="~/capsule-corp")


@pytest.fixture
def frozen(catalogue: Catalogue) -> CapsuleRef:
    ref = catalogue.create("Runnable", question="Does it run?")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    catalogue.freeze(ref)
    return ref


# ------------------------------------------------------------------- registry


@pytest.mark.parametrize("name", EXECUTOR_NAMES)
def test_every_named_executor_can_be_constructed(name: str) -> None:
    executor = get_executor(name, Settings())
    assert executor.name == name


def test_unknown_executor_is_rejected() -> None:
    with pytest.raises(ExecutionError, match="unknown executor"):
        get_executor("kubernetes", Settings())


def test_remote_executors_report_why_they_are_unusable() -> None:
    """An unconfigured host must produce a message, not a traceback at run time."""
    usable, reason = get_executor("slurm", Settings()).available()
    assert not usable
    assert "slurm.host" in reason


# ----------------------------------------------------------------------- local


def test_local_run_produces_results(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    (frozen.path / "run.py").write_text(
        "import json, pathlib\n"
        "pathlib.Path('results').mkdir(exist_ok=True)\n"
        "pathlib.Path('results/results.json').write_text(json.dumps({'slope': -0.5}))\n"
        "print('done')\n",
        encoding="utf-8",
    )
    outcome = LocalExecutor().execute(catalogue, frozen, Settings(), ExecutionSpec(timeout_seconds=60))
    assert outcome.ok
    assert outcome.executor == "local"
    assert json.loads(frozen.results_json.read_text(encoding="utf-8")) == {"slope": -0.5}
    assert "done" in outcome.stdout_path.read_text(encoding="utf-8")
    assert catalogue.get(frozen.capsule.id).capsule.status is CapsuleStatus.RUN


def test_local_run_reports_failure_without_advancing_status(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    (frozen.path / "run.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    outcome = LocalExecutor().execute(catalogue, frozen, Settings(), ExecutionSpec(timeout_seconds=60))
    assert not outcome.ok
    assert outcome.exit_code == 3
    assert catalogue.get(frozen.capsule.id).capsule.status is not CapsuleStatus.RUN


def test_local_run_times_out(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    (frozen.path / "run.py").write_text("import time; time.sleep(30)\n", encoding="utf-8")
    outcome = LocalExecutor().execute(catalogue, frozen, Settings(), ExecutionSpec(timeout_seconds=1))
    assert not outcome.ok
    assert "exceeded" in (outcome.error or "")
    assert "timed out" in outcome.stdout_path.read_text(encoding="utf-8")


def test_local_run_requires_run_py(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    with pytest.raises(ExecutionError, match="no run.py"):
        LocalExecutor().execute(catalogue, frozen, Settings(), ExecutionSpec())


def test_local_run_checks_the_freeze(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    (frozen.path / "run.py").write_text("print(1)\n", encoding="utf-8")
    frozen.prereg_path.write_text('hypothesis = "changed"\n', encoding="utf-8")
    with pytest.raises(Exception, match="changed after freezing"):
        LocalExecutor().execute(catalogue, frozen, Settings(), ExecutionSpec())


def test_capsule_command_falls_back_to_python(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    assert capsule_command(frozen) == ["python", "run.py"]
    (frozen.path / "pixi.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert capsule_command(frozen)[0] in {"pixi", "python"}


# ------------------------------------------------------------------- transport


def test_rsync_push_excludes_environments_and_history() -> None:
    argv = rsync_push_command(Path("/tmp/caps/0001-x"), REMOTE, "0001-x")
    assert argv[0] == "rsync"
    for pattern in (".pixi/", ".git/", "runs/"):
        assert pattern in argv
    # Trailing slashes copy contents rather than nesting a directory.
    assert argv[-2].endswith("/")
    assert argv[-1] == "sherlock:~/capsule-corp/0001-x/"


def test_rsync_pull_targets_results() -> None:
    argv = rsync_pull_command(REMOTE, "0001-x", Path("/tmp/caps/0001-x"))
    assert argv[-2] == "sherlock:~/capsule-corp/0001-x/results/"
    assert argv[-1].endswith("/results/")


def test_ssh_and_mkdir_commands() -> None:
    assert ssh_command(REMOTE, "echo hi") == ["ssh", "sherlock", "echo hi"]
    assert mkdir_command(REMOTE, "0001-x") == ["ssh", "sherlock", "mkdir -p ~/capsule-corp/0001-x"]


# ----------------------------------------------------------------------- slurm


def test_sbatch_script_carries_resources_and_offline() -> None:
    settings = Settings()
    settings.executors.slurm.partition = "gpu"
    settings.executors.slurm.account = "mlgroup"
    script = build_sbatch_script(
        "0001-x", "~/capsule-corp/0001-x", settings.executors.slurm, ExecutionSpec(cpus=8, memory="32G", gpus=2)
    )
    assert "#SBATCH --cpus-per-task=8" in script
    assert "#SBATCH --mem=32G" in script
    assert "#SBATCH --gpus=2" in script
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --account=mlgroup" in script
    # Compute nodes generally have no outbound network.
    assert "export PI_OFFLINE=1" in script
    assert script.startswith("#!/bin/bash")


def test_sbatch_script_omits_gpus_when_none_requested() -> None:
    script = build_sbatch_script("0001-x", "/p", Settings().executors.slurm, ExecutionSpec(gpus=0))
    assert "--gpus" not in script


def test_sbatch_script_falls_back_to_python_without_pixi() -> None:
    script = build_sbatch_script("0001-x", "/p", Settings().executors.slurm, ExecutionSpec())
    assert "pixi run run" in script
    assert "python run.py" in script


@pytest.mark.parametrize(
    ("output", "expected"),
    [("Submitted batch job 12345\n", "12345"), ("noise\nSubmitted batch job 987\n", "987")],
)
def test_job_id_parsing(output: str, expected: str) -> None:
    match = JOB_ID_PATTERN.search(output)
    assert match is not None
    assert match.group(1) == expected


def test_job_id_parsing_rejects_garbage() -> None:
    assert JOB_ID_PATTERN.search("sbatch: error: invalid partition") is None


def test_slurm_refuses_without_a_host(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    executor = SlurmExecutor(Settings().executors.slurm)
    usable, reason = executor.available()
    assert not usable and "host" in reason


# ------------------------------------------------------------------ ssh+docker


def test_docker_command_mounts_and_limits() -> None:
    command = build_docker_command("~/capsule-corp/0001-x", "myimage:1", ExecutionSpec(cpus=2, memory="4G"))
    assert "docker run --rm" in command
    assert "-v ~/capsule-corp/0001-x:/capsule" in command
    assert "-w /capsule" in command
    assert "--cpus=2" in command
    assert "--memory=4G" in command
    assert command.endswith("'")
    assert "myimage:1" in command


def test_docker_command_adds_gpus_only_when_requested() -> None:
    assert "--gpus" not in build_docker_command("/p", "img", ExecutionSpec(gpus=0))
    assert "--gpus 1" in build_docker_command("/p", "img", ExecutionSpec(gpus=1))


def test_ssh_executor_needs_a_host() -> None:
    usable, reason = SshDockerExecutor(Settings().executors.ssh).available()
    assert not usable and "host" in reason


# ----------------------------------------------------------------------- modal


def test_capsule_packages_read_from_the_capsules_own_manifest(frozen: CapsuleRef) -> None:
    (frozen.path / "pixi.toml").write_text(
        '[project]\nname = "x"\n\n[dependencies]\npython = "3.12"\nnumpy = "*"\nscipy = ">=1.11"\n',
        encoding="utf-8",
    )
    assert set(capsule_packages(frozen)) == {"python=3.12", "numpy", "scipy>=1.11"}


def test_capsule_packages_defaults_without_a_manifest(frozen: CapsuleRef) -> None:
    assert capsule_packages(frozen) == ["python=3.12"]


def test_extract_results_refuses_path_traversal(tmp_path: Path) -> None:
    """The archive is built off-machine, so it is not trusted."""
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = tmp_path / "payload.txt"
        payload.write_text("x", encoding="utf-8")
        archive.add(payload, arcname="../escaped.txt")
        archive.add(payload, arcname="results/fine.txt")

    destination = tmp_path / "out"
    extracted = extract_results(buffer.getvalue(), destination)
    assert extracted == ["results/fine.txt"]
    assert not (tmp_path / "escaped.txt").exists()


# ------------------------------------------------------------------------ spec


def test_spec_takes_cluster_defaults_from_settings() -> None:
    settings = Settings()
    settings.executors.slurm.cpus = 16
    settings.executors.slurm.gpus = 4
    spec = spec_from_settings(settings, "slurm")
    assert spec.cpus == 16 and spec.gpus == 4


def test_spec_timeout_override() -> None:
    assert spec_from_settings(Settings(), "local", timeout_seconds=42).timeout_seconds == 42
