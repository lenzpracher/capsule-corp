"""Per-capsule environment and agent configuration.

Both files written here live inside the capsule and are committed with it, so a capsule
carries its own pinned tooling rather than depending on whatever the machine happens to
have configured.
"""

from __future__ import annotations

import json
from typing import Any

import tomlkit

from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef

PI_DIR = ".pi"
PI_SETTINGS = "settings.json"


def _split_spec(package: str) -> tuple[str, str]:
    """Split ``numpy>=2`` or ``python=3.12`` into a name and a pixi version spec."""
    for separator in ("==", ">=", "<=", "!=", ">", "<", "="):
        name, found, version = package.partition(separator)
        if found:
            # pixi wants '==' style specs written plainly; a bare '=' means 'this version'.
            spec = version if separator == "=" else f"{separator}{version}"
            return name.strip(), spec.strip()
    return package.strip(), "*"


def write_pixi_manifest(ref: CapsuleRef, settings: Settings, *, overwrite: bool = False) -> bool:
    """Write the capsule's ``pixi.toml``. Returns True if it was written."""
    target = ref.path / "pixi.toml"
    if target.is_file() and not overwrite:
        return False

    dependencies: dict[str, str] = {}
    for package in settings.pixi.default_packages:
        name, spec = _split_spec(package)
        dependencies[name] = spec

    manifest: dict[str, Any] = {
        "project": {
            "name": ref.capsule.dirname,
            "version": "0.1.0",
            "description": ref.capsule.title,
            "channels": list(settings.pixi.channels),
            "platforms": list(settings.pixi.platforms),
        },
        "dependencies": dependencies,
        "tasks": {"run": "python run.py"},
    }
    target.write_text(tomlkit.dumps(manifest), encoding="utf-8")
    return True


def write_pi_settings(ref: CapsuleRef, settings: Settings, *, overwrite: bool = False) -> bool:
    """Write the capsule's project-local ``pi`` configuration.

    Only values that are explicitly pinned are written. An empty object is still written
    so there is an obvious place to pin a model when a capsule needs to be reproduced
    exactly, rather than inheriting whatever pi is set to that day.

    Note that pi only reads this file when the project is trusted; capsule-corp always
    invokes pi with ``--approve`` for that reason.
    """
    target = ref.path / PI_DIR / PI_SETTINGS
    if target.is_file() and not overwrite:
        return False

    pinned: dict[str, Any] = {}
    if settings.agent.provider:
        pinned["defaultProvider"] = settings.agent.provider
    if settings.agent.model:
        pinned["defaultModel"] = settings.agent.model
    if settings.agent.thinking:
        pinned["defaultThinkingLevel"] = settings.agent.thinking

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(pinned, indent=2) + "\n", encoding="utf-8")
    return True


AGENTS_TEMPLATE = """\
# {title}

This is a capsule-corp research capsule. It packages one empirically testable question
so that the result is reproducible and cannot be quietly retrofitted to the conclusion.

## Rules

- `prereg.toml` and `.prereg.lock` are **read-only**. They record what was predicted
  before any code existed. Their hash is verified before and after every agent phase.
- `results/results.json` must contain the real measured values, and exactly the keys
  declared in the `[results_contract]` table of `prereg.toml`.
- Every random seed must be set and recorded. The same command must produce the same
  numbers.
- A refuted hypothesis is a successful capsule. Report what happened.

## Layout

- `QUESTION.md` — the research question and design
- `prereg.toml` — frozen hypothesis, predictions, and checks
- `run.py` — entry point; `pixi run run` executes the experiment
- `results/` — `results.json` and `figures/`
- `REPORT.md` — the write-up
"""


def write_agents_md(ref: CapsuleRef, *, overwrite: bool = False) -> bool:
    """Write the capsule's agent instructions.

    Named ``AGENTS.md`` rather than ``CLAUDE.md`` because the runner is deliberately not
    tied to one vendor; pi reads both.
    """
    target = ref.path / "AGENTS.md"
    if target.is_file() and not overwrite:
        return False
    target.write_text(AGENTS_TEMPLATE.format(title=ref.capsule.title), encoding="utf-8")
    return True


def scaffold_capsule(ref: CapsuleRef, settings: Settings, *, overwrite: bool = False) -> list[str]:
    """Write every per-capsule config file. Returns the names of those written."""
    written = []
    if write_pixi_manifest(ref, settings, overwrite=overwrite):
        written.append("pixi.toml")
    if write_pi_settings(ref, settings, overwrite=overwrite):
        written.append(f"{PI_DIR}/{PI_SETTINGS}")
    if write_agents_md(ref, overwrite=overwrite):
        written.append("AGENTS.md")
    return written
