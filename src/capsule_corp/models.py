"""Core data model for capsules.

Files on disk are the source of truth. These models describe what those files mean;
they are deliberately plain so that a human can hand-edit ``capsule.toml`` or
``prereg.toml`` without going through the tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    """Timezone-aware current time, so serialized timestamps are unambiguous."""
    return datetime.now(timezone.utc)


class CapsuleStatus(StrEnum):
    """Lifecycle of a capsule.

    The order matters: a capsule advances through these states and several commands
    refuse to run unless the capsule has reached a given state.
    """

    DRAFT = "draft"
    DESIGNED = "designed"
    FROZEN = "frozen"
    IMPLEMENTED = "implemented"
    RUN = "run"
    VERIFIED = "verified"
    REFUTED = "refuted"
    FAILED = "failed"


# Status a capsule must have reached before the given action is allowed.
_ORDER: dict[CapsuleStatus, int] = {
    CapsuleStatus.DRAFT: 0,
    CapsuleStatus.DESIGNED: 1,
    CapsuleStatus.FROZEN: 2,
    CapsuleStatus.IMPLEMENTED: 3,
    CapsuleStatus.RUN: 4,
    CapsuleStatus.VERIFIED: 5,
    # Terminal outcomes sort alongside VERIFIED: the capsule ran and was judged.
    CapsuleStatus.REFUTED: 5,
    CapsuleStatus.FAILED: 5,
}


def status_rank(status: CapsuleStatus) -> int:
    """Position of ``status`` in the lifecycle, for 'has it got this far?' tests."""
    return _ORDER[status]


class CheckKind(StrEnum):
    """How a single pre-registered check is evaluated."""

    EXPR = "expr"
    ARTIFACT = "artifact"
    SCRIPT = "script"


class Check(BaseModel):
    """One machine-checkable assertion, written by the designer before any code exists.

    A check must be evaluable with no LLM involved. That is what makes it a hard gate
    rather than an opinion.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: CheckKind
    description: str = ""
    # kind == EXPR: a restricted Python expression over ``results``.
    expr: str | None = None
    # kind == ARTIFACT: a capsule-relative path that must exist and be non-empty.
    path: str | None = None
    # kind == SCRIPT: "module:function", resolved inside the capsule's own source.
    script: str | None = None

    @field_validator("id")
    @classmethod
    def _id_is_slug(cls, value: str) -> str:
        if not value or not all(c.isalnum() or c in "-_" for c in value):
            raise ValueError(f"check id must be alphanumeric/dash/underscore, got {value!r}")
        return value

    def required_field(self) -> str:
        """Name of the field this check kind depends on."""
        return {CheckKind.EXPR: "expr", CheckKind.ARTIFACT: "path", CheckKind.SCRIPT: "script"}[self.kind]

    @property
    def is_well_formed(self) -> bool:
        return getattr(self, self.required_field()) is not None


class Prereg(BaseModel):
    """The frozen pre-registration: what we predict, and what would falsify it.

    Written during the design phase, hashed at freeze time, and never modified
    afterwards. :mod:`capsule_corp.store` enforces the hash.
    """

    model_config = ConfigDict(extra="forbid")

    hypothesis: str
    predictions: list[str] = Field(default_factory=list)
    analysis_plan: str = ""
    # Keys the implementation must write into results/results.json, mapped to what each
    # one means. The checks reference these, so the contract is what makes them runnable.
    results_contract: dict[str, str] = Field(default_factory=dict)
    checks: list[Check] = Field(default_factory=list)

    @field_validator("checks")
    @classmethod
    def _checks_are_usable(cls, checks: list[Check]) -> list[Check]:
        seen: set[str] = set()
        for check in checks:
            if check.id in seen:
                raise ValueError(f"duplicate check id {check.id!r}")
            seen.add(check.id)
            if not check.is_well_formed:
                raise ValueError(f"check {check.id!r} of kind {check.kind} needs a {check.required_field()!r} field")
        return checks


class Provenance(BaseModel):
    """What actually produced an artifact.

    capsule-corp never pins a model; it records whichever one ``pi`` was configured
    with, so a capsule stays honest about how it was made.
    """

    model_config = ConfigDict(extra="allow")

    runner: str = "pi"
    runner_version: str | None = None
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    mcp_servers: list[str] = Field(default_factory=list)
    executor: str = "local"
    host: str | None = None
    git_sha: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None


class Capsule(BaseModel):
    """Manifest of a single capsule, persisted as ``capsule.toml``."""

    model_config = ConfigDict(extra="allow")

    id: str
    slug: str
    title: str
    question: str = ""
    status: CapsuleStatus = CapsuleStatus.DRAFT
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    provenance: Provenance = Field(default_factory=Provenance)

    @property
    def dirname(self) -> str:
        """Directory name for this capsule, e.g. ``0012-lr-warmup``."""
        return f"{self.id}-{self.slug}"

    def has_reached(self, status: CapsuleStatus) -> bool:
        """True if this capsule is at least as far along as ``status``."""
        return status_rank(self.status) >= status_rank(status)
