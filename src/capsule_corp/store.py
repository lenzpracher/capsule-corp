"""Filesystem catalogue of capsules.

Files are the source of truth. Everything here reads and writes ordinary text in a
git-friendly layout, so the catalogue survives this tool being rewritten or thrown
away, and stays editable by hand.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit

from capsule_corp.models import Capsule, CapsuleStatus, Prereg, Provenance, utcnow

CAPSULES_DIR = "capsules"
STATE_DIR = ".capsule-corp"
MANIFEST = "capsule.toml"
PREREG = "prereg.toml"
PREREG_LOCK = ".prereg.lock"
QUESTION = "QUESTION.md"
AGENTS = "AGENTS.md"
RESULTS = "results"
RUNS = "runs"
VERIFICATION = "verification.json"
REPORT = "REPORT.md"


class CatalogueError(Exception):
    """Base class for catalogue problems that should reach the user as a clean message."""


class CapsuleNotFoundError(CatalogueError):
    pass


class DuplicateCapsuleError(CatalogueError):
    pass


class PreregTamperError(CatalogueError):
    """Raised when a frozen pre-registration no longer matches its lock.

    This is the mechanism that makes a capsule pre-registered rather than merely
    documented: once frozen, the predictions and checks cannot be quietly rewritten
    to match whatever the implementation happened to produce.
    """


class NotFrozenError(CatalogueError):
    pass


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 48) -> str:
    """Turn a title into a short, filesystem- and URL-safe slug."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", normalized.lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def _git_sha(root: Path) -> str | None:
    """Current commit of the catalogue repo, or None if this isn't a git checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _dump_toml(data: dict[str, Any], path: Path) -> None:
    path.write_text(tomlkit.dumps(data), encoding="utf-8")


def _load_toml(path: Path) -> dict[str, Any]:
    return dict(tomlkit.parse(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class CapsuleRef:
    """A capsule together with where it lives."""

    capsule: Capsule
    path: Path
    folder: str

    @property
    def prereg_path(self) -> Path:
        return self.path / PREREG

    @property
    def lock_path(self) -> Path:
        return self.path / PREREG_LOCK

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST

    @property
    def results_path(self) -> Path:
        return self.path / RESULTS

    @property
    def results_json(self) -> Path:
        return self.path / RESULTS / "results.json"

    @property
    def runs_path(self) -> Path:
        return self.path / RUNS

    @property
    def verification_path(self) -> Path:
        return self.path / VERIFICATION


class Catalogue:
    """A directory tree of capsules rooted at ``root/capsules``."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # ------------------------------------------------------------------ discovery

    @classmethod
    def init(cls, root: Path) -> Catalogue:
        """Create the catalogue directories if they do not already exist."""
        root = root.resolve()
        (root / CAPSULES_DIR).mkdir(parents=True, exist_ok=True)
        (root / STATE_DIR).mkdir(parents=True, exist_ok=True)
        gitkeep = root / CAPSULES_DIR / ".gitkeep"
        if not any(p for p in (root / CAPSULES_DIR).iterdir() if p.name != ".gitkeep"):
            gitkeep.touch()
        return cls(root)

    @classmethod
    def discover(cls, start: Path | None = None) -> Catalogue:
        """Walk up from ``start`` looking for a catalogue root."""
        current = (start or Path.cwd()).resolve()
        for candidate in [current, *current.parents]:
            if (candidate / STATE_DIR).is_dir() or (candidate / CAPSULES_DIR).is_dir():
                return cls(candidate)
        raise CatalogueError(
            "no capsule catalogue found here or in any parent directory; run 'capsule init' to create one"
        )

    @property
    def capsules_root(self) -> Path:
        return self.root / CAPSULES_DIR

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    # ------------------------------------------------------------------- reading

    def iter_capsules(self) -> Iterator[CapsuleRef]:
        """Every capsule in the tree, ordered by id."""
        if not self.capsules_root.is_dir():
            return
        refs: list[CapsuleRef] = []
        for manifest in sorted(self.capsules_root.rglob(MANIFEST)):
            try:
                refs.append(self._load_ref(manifest.parent))
            except CatalogueError:
                continue
        for ref in sorted(refs, key=lambda r: r.capsule.id):
            yield ref

    def _load_ref(self, directory: Path) -> CapsuleRef:
        manifest = directory / MANIFEST
        if not manifest.is_file():
            raise CapsuleNotFoundError(f"{directory} is not a capsule (no {MANIFEST})")
        capsule = Capsule.model_validate(_load_toml(manifest))
        folder = directory.parent.relative_to(self.capsules_root).as_posix()
        return CapsuleRef(capsule=capsule, path=directory, folder="" if folder == "." else folder)

    def get(self, ident: str) -> CapsuleRef:
        """Look a capsule up by id, directory name, or slug."""
        matches = [
            ref for ref in self.iter_capsules() if ident in (ref.capsule.id, ref.capsule.dirname, ref.capsule.slug)
        ]
        if not matches:
            raise CapsuleNotFoundError(f"no capsule matching {ident!r}")
        if len(matches) > 1:
            names = ", ".join(r.capsule.dirname for r in matches)
            raise DuplicateCapsuleError(f"{ident!r} is ambiguous: {names}")
        return matches[0]

    def folders(self) -> list[str]:
        """Every folder in the catalogue, as posix-relative paths."""
        if not self.capsules_root.is_dir():
            return []
        found: set[str] = set()
        for path in self.capsules_root.rglob("*"):
            if path.is_dir() and not (path / MANIFEST).is_file() and not self._inside_capsule(path):
                found.add(path.relative_to(self.capsules_root).as_posix())
        return sorted(found)

    def _inside_capsule(self, path: Path) -> bool:
        """True if ``path`` is nested within a capsule directory rather than a plain folder."""
        for parent in path.parents:
            if parent == self.capsules_root:
                return False
            if (parent / MANIFEST).is_file():
                return True
        return False

    # ------------------------------------------------------------------- writing

    def next_id(self) -> str:
        """Allocate the next zero-padded capsule id."""
        highest = 0
        for ref in self.iter_capsules():
            if ref.capsule.id.isdigit():
                highest = max(highest, int(ref.capsule.id))
        return f"{highest + 1:04d}"

    def make_folder(self, relative: str) -> Path:
        """Create a folder inside the catalogue."""
        target = self._resolve_folder(relative)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _resolve_folder(self, relative: str) -> Path:
        """Resolve a user-supplied folder path, refusing anything outside the catalogue."""
        target = (self.capsules_root / relative).resolve()
        if target != self.capsules_root and self.capsules_root not in target.parents:
            raise CatalogueError(f"folder {relative!r} is outside the catalogue")
        return target

    def create(self, title: str, folder: str = "", question: str = "") -> CapsuleRef:
        """Create a new capsule skeleton on disk."""
        self.capsules_root.mkdir(parents=True, exist_ok=True)
        parent = self._resolve_folder(folder) if folder else self.capsules_root
        parent.mkdir(parents=True, exist_ok=True)

        capsule = Capsule(id=self.next_id(), slug=slugify(title), title=title, question=question)
        directory = parent / capsule.dirname
        if directory.exists():
            raise DuplicateCapsuleError(f"{directory} already exists")
        directory.mkdir(parents=True)
        (directory / RESULTS).mkdir()
        (directory / RUNS).mkdir()

        rel = directory.parent.relative_to(self.capsules_root).as_posix()
        ref = CapsuleRef(capsule=capsule, path=directory, folder="" if rel == "." else rel)
        self.save(ref)
        (directory / QUESTION).write_text(
            f"# {title}\n\n{question or '<!-- The research question, written by the design phase. -->'}\n",
            encoding="utf-8",
        )
        return ref

    def save(self, ref: CapsuleRef) -> None:
        """Persist the manifest, refreshing ``updated_at``."""
        ref.capsule.updated_at = utcnow()
        _dump_toml(ref.capsule.model_dump(mode="json", exclude_none=True), ref.manifest_path)

    def set_status(self, ref: CapsuleRef, status: CapsuleStatus) -> None:
        ref.capsule.status = status
        self.save(ref)

    def move(self, ident: str, dest_folder: str) -> CapsuleRef:
        """Move a capsule into another folder."""
        ref = self.get(ident)
        destination = self._resolve_folder(dest_folder) if dest_folder else self.capsules_root
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / ref.capsule.dirname
        if target.exists():
            raise DuplicateCapsuleError(f"{target} already exists")
        ref.path.rename(target)
        return self._load_ref(target)

    def remove(self, ident: str) -> Path:
        """Delete a capsule directory and return where it used to be."""
        import shutil

        ref = self.get(ident)
        shutil.rmtree(ref.path)
        return ref.path

    # ------------------------------------------------------------------- runs

    def new_run_dir(self, ref: CapsuleRef, phase: str) -> Path:
        """Create a fresh timestamped directory for one agent phase's artifacts."""
        stamp = utcnow().strftime("%Y%m%dT%H%M%S")
        base = ref.runs_path / f"{stamp}-{phase}"
        candidate = base
        suffix = 1
        while candidate.exists():
            candidate = ref.runs_path / f"{stamp}-{phase}-{suffix}"
            suffix += 1
        candidate.mkdir(parents=True)
        return candidate

    def write_run_meta(
        self,
        run_dir: Path,
        *,
        phase: str,
        provenance: Provenance,
        ok: bool,
        error: str | None = None,
    ) -> Path:
        """Record who produced this run and what it cost, next to its event stream."""
        meta = {
            "phase": phase,
            "ok": ok,
            "error": error,
            "git_sha": _git_sha(self.root),
            "provenance": provenance.model_dump(mode="json", exclude_none=True),
        }
        target = run_dir / "meta.json"
        target.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return target

    # ------------------------------------------------------------- pre-registration

    def load_prereg(self, ref: CapsuleRef) -> Prereg | None:
        """Read ``prereg.toml``, or None if the design phase hasn't run yet."""
        if not ref.prereg_path.is_file():
            return None
        raw = _load_toml(ref.prereg_path)
        # Accept a singular [[check]] table as well, since that reads more naturally.
        if "check" in raw and "checks" not in raw:
            raw["checks"] = raw.pop("check")
        return Prereg.model_validate(raw)

    def write_prereg(self, ref: CapsuleRef, prereg: Prereg) -> None:
        """Write ``prereg.toml``. Refuses to overwrite a frozen pre-registration."""
        if ref.lock_path.is_file():
            raise PreregTamperError(
                f"capsule {ref.capsule.id} is frozen; its pre-registration cannot be rewritten. "
                "Create a new capsule instead of changing a registered prediction."
            )
        _dump_toml(prereg.model_dump(mode="json", exclude_none=True), ref.prereg_path)

    def prereg_digest(self, ref: CapsuleRef) -> str:
        """SHA-256 of the raw pre-registration bytes."""
        if not ref.prereg_path.is_file():
            raise NotFrozenError(f"capsule {ref.capsule.id} has no {PREREG} to hash")
        return hashlib.sha256(ref.prereg_path.read_bytes()).hexdigest()

    def freeze(self, ref: CapsuleRef) -> str:
        """Lock the pre-registration, recording its hash. Returns the digest."""
        prereg = self.load_prereg(ref)
        if prereg is None:
            raise NotFrozenError(f"capsule {ref.capsule.id} has no {PREREG}; run the design phase first")
        if not prereg.checks:
            raise NotFrozenError(
                f"capsule {ref.capsule.id} declares no checks; a capsule with nothing falsifiable cannot be frozen"
            )
        digest = self.prereg_digest(ref)
        _dump_toml(
            {
                "sha256": digest,
                "frozen_at": utcnow().isoformat(),
                "git_sha": _git_sha(self.root) or "",
                "checks": [c.id for c in prereg.checks],
            },
            ref.lock_path,
        )
        self.set_status(ref, CapsuleStatus.FROZEN)
        return digest

    def is_frozen(self, ref: CapsuleRef) -> bool:
        return ref.lock_path.is_file()

    def verify_frozen(self, ref: CapsuleRef) -> None:
        """Raise if the pre-registration has drifted from its lock.

        Called before and after every agent phase that could touch the capsule.
        """
        if not ref.lock_path.is_file():
            raise NotFrozenError(f"capsule {ref.capsule.id} is not frozen; run 'capsule freeze {ref.capsule.id}'")
        recorded = str(_load_toml(ref.lock_path).get("sha256", ""))
        actual = self.prereg_digest(ref)
        if recorded != actual:
            raise PreregTamperError(
                f"pre-registration of capsule {ref.capsule.id} changed after freezing "
                f"(expected {recorded[:12]}, found {actual[:12]}). "
                "Restore it from git, or create a new capsule for the revised prediction."
            )
