from __future__ import annotations

import pytest

from capsule_corp.index import Index
from capsule_corp.store import REPORT, Catalogue


def test_rebuild_and_search(catalogue: Catalogue) -> None:
    ref = catalogue.create("Learning rate warmup", question="Does warmup reduce final loss?")
    (ref.path / REPORT).write_text("We found that warmup helps on small batches.", encoding="utf-8")
    catalogue.create("Unrelated topic", question="Something about tokenizers.")

    index = Index.for_catalogue(catalogue)
    assert index.rebuild(catalogue) == 2

    hits = index.search("warmup")
    assert [h.id for h in hits] == ["0001"]

    # The report body is searchable too, not just the question.
    assert [h.id for h in index.search("batches")] == ["0001"]


def test_rebuild_is_idempotent(catalogue: Catalogue) -> None:
    catalogue.create("Once", question="q")
    index = Index.for_catalogue(catalogue)
    index.rebuild(catalogue)
    index.rebuild(catalogue)
    assert len(index.search("q")) <= 1


def test_index_is_disposable(catalogue: Catalogue) -> None:
    """Deleting the database must never lose a capsule."""
    catalogue.create("Durable", question="still here")
    index = Index.for_catalogue(catalogue)
    index.rebuild(catalogue)
    index.path.unlink()
    assert index.rebuild(catalogue) == 1


def test_malformed_query_is_user_error(catalogue: Catalogue) -> None:
    index = Index.for_catalogue(catalogue)
    index.rebuild(catalogue)
    with pytest.raises(ValueError, match="invalid search query"):
        index.search('"unbalanced')
