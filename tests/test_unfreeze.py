"""Tests for unfreezing.

Unfreezing is an escape hatch that could, if silent, make the whole pre-registration
scheme decorative. These tests are about the audit trail, not the mechanics.
"""

from __future__ import annotations

import json

import pytest

from capsule_corp.models import CapsuleStatus
from capsule_corp.phases.design import design
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError, NotFrozenError
from tests.helpers import ScriptedRunner, _writes


@pytest.fixture
def frozen(catalogue: Catalogue) -> CapsuleRef:
    ref = catalogue.create("Revisable", question="Does it hold?")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    catalogue.freeze(ref)
    return ref


def test_unfreeze_releases_the_lock(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    catalogue.unfreeze(frozen, reason="a check referenced the wrong key")
    assert not catalogue.is_frozen(frozen)
    assert catalogue.get(frozen.capsule.id).capsule.status is CapsuleStatus.DESIGNED


def test_unfreeze_allows_redesign_and_refreeze(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    catalogue.unfreeze(frozen, reason="typo")
    design(catalogue, frozen, ScriptedRunner(_writes()), Settings())
    assert catalogue.freeze(frozen)


def test_unfreeze_archives_the_superseded_version(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    original = frozen.prereg_path.read_text(encoding="utf-8")
    archive = catalogue.unfreeze(frozen, reason="tolerance was wrong")
    assert (archive / "prereg.toml").read_text(encoding="utf-8") == original
    assert (archive / ".prereg.lock").is_file()


def test_unfreeze_records_the_reason(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    catalogue.unfreeze(frozen, reason="the figure path was misspelled")
    revisions = catalogue.revisions(frozen)
    assert len(revisions) == 1
    assert revisions[0]["reason"] == "the figure path was misspelled"
    assert len(revisions[0]["superseded_sha256"]) == 64


def test_unfreeze_requires_a_reason(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    with pytest.raises(CatalogueError, match="requires a reason"):
        catalogue.unfreeze(frozen, reason="   ")


def test_unfreeze_refuses_when_not_frozen(catalogue: Catalogue) -> None:
    ref = catalogue.create("Never frozen")
    with pytest.raises(NotFrozenError, match="not frozen"):
        catalogue.unfreeze(ref, reason="whatever")


def test_unfreeze_notes_whether_results_already_existed(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    """Revising before results is housekeeping; revising after is a different act."""
    catalogue.unfreeze(frozen, reason="before any results")
    assert catalogue.revisions(frozen)[0]["results_existed"] is False

    catalogue.freeze(frozen)
    frozen.results_json.parent.mkdir(parents=True, exist_ok=True)
    frozen.results_json.write_text(json.dumps({"slope": -0.9}), encoding="utf-8")
    catalogue.unfreeze(frozen, reason="did not like the outcome")

    revisions = catalogue.revisions(frozen)
    assert len(revisions) == 2
    assert revisions[1]["results_existed"] is True


def test_unfreeze_invalidates_a_prior_verification(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    """A verdict against superseded predictions must not linger as if it still applied."""
    frozen.verification_path.write_text('{"status": "verified"}', encoding="utf-8")
    archive = catalogue.unfreeze(frozen, reason="revising")
    assert not frozen.verification_path.exists()
    assert (archive / "verification.json").is_file()


def test_revisions_accumulate_in_order(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    catalogue.unfreeze(frozen, reason="first")
    catalogue.freeze(frozen)
    catalogue.unfreeze(frozen, reason="second")
    assert [r["reason"] for r in catalogue.revisions(frozen)] == ["first", "second"]


def test_no_revisions_on_an_untouched_capsule(catalogue: Catalogue, frozen: CapsuleRef) -> None:
    assert catalogue.revisions(frozen) == []
