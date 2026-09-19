"""SQLite search index over the catalogue.

The index is a cache, never a source of truth: it is derived entirely from files on
disk and can be deleted and rebuilt at any time with ``capsule reindex``. That keeps
the catalogue portable and means a corrupted database is never a lost capsule.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from capsule_corp.store import REPORT, CapsuleRef, Catalogue

SCHEMA = """
CREATE TABLE IF NOT EXISTS capsules (
    id         TEXT PRIMARY KEY,
    slug       TEXT NOT NULL,
    title      TEXT NOT NULL,
    question   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,
    folder     TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS capsules_fts USING fts5(
    id UNINDEXED,
    title,
    question,
    report,
    tokenize = 'porter'
);
"""


@dataclass(frozen=True)
class SearchHit:
    id: str
    title: str
    folder: str
    status: str
    snippet: str


class Index:
    """Rebuildable SQLite index. Safe to delete; see :meth:`rebuild`."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def for_catalogue(cls, catalogue: Catalogue) -> Index:
        catalogue.state_dir.mkdir(parents=True, exist_ok=True)
        return cls(catalogue.state_dir / "index.db")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        return connection

    def rebuild(self, catalogue: Catalogue) -> int:
        """Drop and repopulate the index from disk. Returns the number of capsules."""
        refs = list(catalogue.iter_capsules())
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM capsules")
            connection.execute("DELETE FROM capsules_fts")
            self._insert_many(connection, refs)
        return len(refs)

    @staticmethod
    def _insert_many(connection: sqlite3.Connection, refs: Iterable[CapsuleRef]) -> None:
        for ref in refs:
            capsule = ref.capsule
            report_path = ref.path / REPORT
            report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
            connection.execute(
                "INSERT OR REPLACE INTO capsules (id, slug, title, question, status, folder, tags, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    capsule.id,
                    capsule.slug,
                    capsule.title,
                    capsule.question,
                    str(capsule.status),
                    ref.folder,
                    ",".join(capsule.tags),
                    capsule.updated_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO capsules_fts (id, title, question, report) VALUES (?, ?, ?, ?)",
                (capsule.id, capsule.title, capsule.question, report),
            )

    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        """Full-text search across titles, questions, and reports."""
        sql = (
            "SELECT c.id, c.title, c.folder, c.status,"
            "       snippet(capsules_fts, 2, '[', ']', ' ... ', 12) AS snippet"
            " FROM capsules_fts f JOIN capsules c ON c.id = f.id"
            " WHERE capsules_fts MATCH ? ORDER BY rank LIMIT ?"
        )
        with closing(self._connect()) as connection:
            try:
                rows = connection.execute(sql, (query, limit)).fetchall()
            except sqlite3.OperationalError as exc:
                # A malformed FTS query is user error, not a crash.
                raise ValueError(f"invalid search query {query!r}: {exc}") from exc
        return [
            SearchHit(id=r["id"], title=r["title"], folder=r["folder"], status=r["status"], snippet=r["snippet"])
            for r in rows
        ]
