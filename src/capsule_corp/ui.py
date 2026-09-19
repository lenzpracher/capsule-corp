"""Presentation helpers shared by the CLI and the TUI."""

from __future__ import annotations

from capsule_corp.models import CapsuleStatus

STATUS_STYLE: dict[CapsuleStatus, str] = {
    CapsuleStatus.DRAFT: "dim",
    CapsuleStatus.DESIGNED: "cyan",
    CapsuleStatus.FROZEN: "yellow",
    CapsuleStatus.IMPLEMENTED: "blue",
    CapsuleStatus.RUN: "blue",
    CapsuleStatus.VERIFIED: "green",
    # A refuted hypothesis is a completed result, so it gets its own colour rather
    # than sharing one with failure.
    CapsuleStatus.REFUTED: "magenta",
    CapsuleStatus.FAILED: "red",
}

STATUS_GLYPH: dict[CapsuleStatus, str] = {
    CapsuleStatus.DRAFT: "○",
    CapsuleStatus.DESIGNED: "◔",
    CapsuleStatus.FROZEN: "🔒",
    CapsuleStatus.IMPLEMENTED: "◑",
    CapsuleStatus.RUN: "◕",
    CapsuleStatus.VERIFIED: "✓",
    CapsuleStatus.REFUTED: "✗",
    CapsuleStatus.FAILED: "!",
}


def styled_status(status: CapsuleStatus) -> str:
    """Rich markup for a status label."""
    return f"[{STATUS_STYLE[status]}]{status}[/]"


def status_glyph(status: CapsuleStatus) -> str:
    """A single character standing in for a status, for dense tree rows."""
    return STATUS_GLYPH[status]
