"""Tests for the filesystem catalogue, with emphasis on the freeze guarantee."""

from __future__ import annotations

from pathlib import Path

import pytest

from capsule_corp.models import Capsule, CapsuleStatus, Check, CheckKind, Prereg
from capsule_corp.store import (
    CapsuleNotFoundError,
    Catalogue,
    DuplicateCapsuleError,
    NotFrozenError,
    PreregTamperError,
    slugify,
)


def _prereg() -> Prereg:
    return Prereg(
        hypothesis="Warmup lowers final loss.",
        predictions=["final loss with warmup is strictly lower"],
        checks=[Check(id="direction", kind=CheckKind.EXPR, expr="results.a < results.b")],
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Does LR warmup matter?", "does-lr-warmup-matter"),
        ("  Ünïcode  tïtle ", "unicode-title"),
        ("!!!", "untitled"),
        ("a" * 80, "a" * 48),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected


def test_discover_walks_up(catalogue: Catalogue) -> None:
    nested = catalogue.root / "a" / "b"
    nested.mkdir(parents=True)
    assert Catalogue.discover(nested).root == catalogue.root


def test_discover_raises_outside_catalogue(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="no capsule catalogue found"):
        Catalogue.discover(tmp_path)


def test_create_allocates_sequential_ids(catalogue: Catalogue) -> None:
    first = catalogue.create("First question")
    second = catalogue.create("Second question")
    assert first.capsule.id == "0001"
    assert second.capsule.id == "0002"
    assert first.path.name == "0001-first-question"


def test_create_in_folder_and_move(catalogue: Catalogue) -> None:
    ref = catalogue.create("Scaling", folder="optimization")
    assert ref.folder == "optimization"
    moved = catalogue.move("0001", "archive")
    assert moved.folder == "archive"
    assert not (catalogue.capsules_root / "optimization" / "0001-scaling").exists()


def test_create_rejects_escaping_folder(catalogue: Catalogue) -> None:
    with pytest.raises(Exception, match="outside the catalogue"):
        catalogue.create("Escape", folder="../../etc")


def test_get_by_id_slug_and_dirname(catalogue: Catalogue) -> None:
    catalogue.create("Warmup study")
    assert catalogue.get("0001").capsule.id == "0001"
    assert catalogue.get("warmup-study").capsule.id == "0001"
    assert catalogue.get("0001-warmup-study").capsule.id == "0001"
    with pytest.raises(CapsuleNotFoundError):
        catalogue.get("nope")


def test_duplicate_directory_rejected(catalogue: Catalogue) -> None:
    """A stray directory occupying the next capsule's name is reported, not clobbered."""
    catalogue.create("Same title")
    # An unrelated directory (no manifest, so not a capsule) squats on the next name.
    (catalogue.capsules_root / "0002-same-title").mkdir()
    with pytest.raises(DuplicateCapsuleError, match="already exists"):
        catalogue.create("Same title")


def test_move_onto_existing_is_rejected(catalogue: Catalogue) -> None:
    catalogue.create("Shared name", folder="a")
    (catalogue.capsules_root / "b" / "0001-shared-name").mkdir(parents=True)
    with pytest.raises(DuplicateCapsuleError, match="already exists"):
        catalogue.move("0001", "b")


def test_folders_ignores_capsule_internals(catalogue: Catalogue) -> None:
    catalogue.create("Inner", folder="group")
    # results/ and runs/ live inside a capsule and must not appear as catalogue folders.
    assert catalogue.folders() == ["group"]


def test_roundtrip_manifest(catalogue: Catalogue) -> None:
    ref = catalogue.create("Roundtrip", question="Does X hold?")
    ref.capsule.tags = ["optim", "toy"]
    catalogue.save(ref)
    reloaded = catalogue.get("0001").capsule
    assert reloaded.tags == ["optim", "toy"]
    assert reloaded.question == "Does X hold?"
    assert isinstance(reloaded, Capsule)


def test_freeze_then_verify_passes(catalogue: Catalogue) -> None:
    ref = catalogue.create("Freeze me")
    catalogue.write_prereg(ref, _prereg())
    digest = catalogue.freeze(ref)
    assert len(digest) == 64
    assert catalogue.is_frozen(ref)
    catalogue.verify_frozen(ref)  # must not raise
    assert catalogue.get("0001").capsule.status is CapsuleStatus.FROZEN


def test_freeze_requires_checks(catalogue: Catalogue) -> None:
    ref = catalogue.create("No checks")
    catalogue.write_prereg(ref, Prereg(hypothesis="Something vague."))
    with pytest.raises(NotFrozenError, match="nothing falsifiable"):
        catalogue.freeze(ref)


def test_freeze_requires_prereg(catalogue: Catalogue) -> None:
    ref = catalogue.create("Nothing registered")
    with pytest.raises(NotFrozenError, match="design phase"):
        catalogue.freeze(ref)


def test_tampering_with_frozen_prereg_is_detected(catalogue: Catalogue) -> None:
    """The core guarantee: a frozen prediction cannot be quietly rewritten."""
    ref = catalogue.create("Tamper")
    catalogue.write_prereg(ref, _prereg())
    catalogue.freeze(ref)

    ref.prereg_path.write_text(
        ref.prereg_path.read_text(encoding="utf-8").replace("results.a < results.b", "True"),
        encoding="utf-8",
    )

    with pytest.raises(PreregTamperError, match="changed after freezing"):
        catalogue.verify_frozen(ref)


def test_write_prereg_refused_after_freeze(catalogue: Catalogue) -> None:
    ref = catalogue.create("Locked")
    catalogue.write_prereg(ref, _prereg())
    catalogue.freeze(ref)
    with pytest.raises(PreregTamperError, match="cannot be rewritten"):
        catalogue.write_prereg(ref, _prereg())


def test_verify_frozen_requires_freeze(catalogue: Catalogue) -> None:
    ref = catalogue.create("Unfrozen")
    catalogue.write_prereg(ref, _prereg())
    with pytest.raises(NotFrozenError, match="not frozen"):
        catalogue.verify_frozen(ref)


def test_prereg_accepts_singular_check_table(catalogue: Catalogue) -> None:
    ref = catalogue.create("Singular")
    ref.prereg_path.write_text(
        'hypothesis = "H"\n\n[[check]]\nid = "c1"\nkind = "artifact"\npath = "results/fig.png"\n',
        encoding="utf-8",
    )
    prereg = catalogue.load_prereg(ref)
    assert prereg is not None
    assert [c.id for c in prereg.checks] == ["c1"]


def test_remove(catalogue: Catalogue) -> None:
    catalogue.create("Doomed")
    removed = catalogue.remove("0001")
    assert not removed.exists()
    assert list(catalogue.iter_capsules()) == []
