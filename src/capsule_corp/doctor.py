"""Environment checks.

Answers "why isn't this working?" in one place, since capsule-corp depends on several
external tools that each fail in their own quiet way.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from capsule_corp.executors import EXECUTOR_NAMES, get_executor
from capsule_corp.settings import Settings

PI_SETTINGS = Path.home() / ".pi" / "agent" / "settings.json"
PI_AUTH = Path.home() / ".pi" / "agent" / "auth.json"
# pi has no built-in MCP support; this third-party extension provides it.
MCP_ADAPTER_PACKAGE = "pi-mcp-adapter"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    advice: str = ""


def _binary(name: str, advice: str = "") -> Check:
    path = shutil.which(name)
    return Check(name, path is not None, path or "not found on PATH", advice if path is None else "")


def _pi_version() -> Check:
    if shutil.which("pi") is None:
        return Check(
            "pi",
            False,
            "not found on PATH",
            "npm install -g @earendil-works/pi-coding-agent",
        )
    try:
        result = subprocess.run(["pi", "--version"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("pi", False, f"could not run pi --version: {exc}")
    return Check("pi", result.returncode == 0, result.stdout.strip() or "unknown version")


def _pi_auth() -> Check:
    """Which providers pi can actually reach.

    capsule-corp never pins a model, so whatever is authenticated here is what will
    design, implement, and judge capsules.
    """
    if not PI_AUTH.is_file():
        return Check("pi auth", False, "no credentials found", "run: pi auth")
    try:
        providers = sorted(json.loads(PI_AUTH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        return Check("pi auth", False, f"could not read {PI_AUTH}: {exc}")
    if not providers:
        return Check("pi auth", False, "no providers authenticated", "run: pi auth")
    return Check("pi auth", True, ", ".join(providers))


def _pi_default_model() -> Check:
    if not PI_SETTINGS.is_file():
        return Check("pi model", True, "no default set; pi will choose at run time")
    try:
        settings = json.loads(PI_SETTINGS.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return Check("pi model", False, f"could not read {PI_SETTINGS}: {exc}")
    provider = settings.get("defaultProvider", "?")
    model = settings.get("defaultModel", "?")
    return Check("pi model", True, f"{provider}/{model}")


def _pi_project_trust() -> Check:
    """The setting that silently disables per-capsule agent configuration.

    pi never prompts for trust in non-interactive modes. capsule-corp always passes
    --approve so this cannot bite, but it is worth surfacing because a capsule's
    .pi/settings.json would otherwise be ignored without any error.
    """
    trust = "ask"
    if PI_SETTINGS.is_file():
        try:
            trust = json.loads(PI_SETTINGS.read_text(encoding="utf-8")).get("defaultProjectTrust", "ask")
        except (json.JSONDecodeError, OSError):
            pass
    return Check(
        "pi project trust",
        True,
        f"{trust} (capsule-corp passes --approve, so per-capsule config still applies)",
    )


def _mcp_adapter() -> Check:
    """Whether pi can consume MCP servers."""
    if shutil.which("npm") is None:
        return Check("pi mcp adapter", False, "npm not found, cannot check", "install Node.js")
    try:
        result = subprocess.run(
            ["npm", "ls", "-g", "--depth=0", "--json"], capture_output=True, text=True, timeout=60, check=False
        )
        installed = MCP_ADAPTER_PACKAGE in (json.loads(result.stdout or "{}").get("dependencies") or {})
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return Check("pi mcp adapter", False, "could not query npm")
    return Check(
        "pi mcp adapter",
        installed,
        "installed" if installed else "not installed",
        "" if installed else f"optional: npm install -g {MCP_ADAPTER_PACKAGE}",
    )


def run_checks(settings: Settings) -> list[Check]:
    """Every environment check, in the order a user should read them."""
    checks = [
        _pi_version(),
        _pi_auth(),
        _pi_default_model(),
        _pi_project_trust(),
        _binary("pixi", "https://pixi.sh"),
        _binary("git"),
        _mcp_adapter(),
    ]
    for name in EXECUTOR_NAMES:
        usable, reason = get_executor(name, settings).available()
        checks.append(Check(f"executor: {name}", usable, "ready" if usable else reason))
    return checks
