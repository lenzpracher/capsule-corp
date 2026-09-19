# Running capsules elsewhere

```bash
capsule run 0001 --on slurm
```

Every backend implements the same `Executor` protocol, so nothing inside a capsule
changes when you move it. `capsule doctor` reports which backends are usable.

## local

The default. Runs `pixi run run` in the capsule directory, falling back to
`python run.py` when the capsule has no pixi environment.

Note that this executes LLM-written code directly on your machine with no sandbox.
That is fine for your own laptop and your own capsules; it is worth thinking about
before you run someone else's.

## slurm

Rsyncs the capsule to a login node, generates an `sbatch` script, submits it, polls
`sacct`, and pulls `results/` back.

```toml
[executors.slurm]
host = "login.sherlock.stanford.edu"
remote_root = "~/capsule-corp"
partition = "gpu"
account = "your-account"
time = "02:00:00"
cpus = 8
mem = "32G"
gpus = 1
```

The generated script sets `PI_OFFLINE=1`, because compute nodes on most clusters have
no outbound network. The capsule's pixi environment must already be installable on the
shared filesystem.

## ssh

Rsyncs the capsule to any host you can reach over SSH and runs it in a container. No
scheduler and no vendor account — a lab workstation is enough.

```toml
[executors.ssh]
host = "workstation.local"
image = "ghcr.io/prefix-dev/pixi:latest"
```

## modal

Serverless compute, with the capsule's conda dependencies rebuilt into the image from
its own `pixi.toml`. Results come back as a tarball.

```toml
[executors.modal]
app_name = "capsule-corp"
gpu = "A10G"
```

**Provisional.** Unlike the SSH and Slurm backends, which speak stable and widely
available protocols, this one is written against the Modal 1.x Python API and has not
been exercised against a live Modal account. Report anything that breaks.
