from __future__ import annotations

import pytest
from pydantic import ValidationError

from capsule_corp.models import Capsule, CapsuleStatus, Check, CheckKind, Prereg


def test_dirname() -> None:
    assert Capsule(id="0007", slug="lr-warmup", title="LR warmup").dirname == "0007-lr-warmup"


def test_has_reached_orders_lifecycle() -> None:
    capsule = Capsule(id="1", slug="s", title="t", status=CapsuleStatus.RUN)
    assert capsule.has_reached(CapsuleStatus.FROZEN)
    assert not capsule.has_reached(CapsuleStatus.VERIFIED)


def test_refuted_counts_as_fully_progressed() -> None:
    """A refuted hypothesis is a completed capsule, not a failed one."""
    capsule = Capsule(id="1", slug="s", title="t", status=CapsuleStatus.REFUTED)
    assert capsule.has_reached(CapsuleStatus.VERIFIED)


def test_check_requires_its_payload() -> None:
    with pytest.raises(ValidationError, match="needs a 'expr'"):
        Prereg(hypothesis="h", checks=[Check(id="c", kind=CheckKind.EXPR)])


def test_duplicate_check_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate check id"):
        Prereg(
            hypothesis="h",
            checks=[
                Check(id="c", kind=CheckKind.ARTIFACT, path="a"),
                Check(id="c", kind=CheckKind.ARTIFACT, path="b"),
            ],
        )


def test_check_id_must_be_sluglike() -> None:
    with pytest.raises(ValidationError):
        Check(id="bad id!", kind=CheckKind.ARTIFACT, path="a")
