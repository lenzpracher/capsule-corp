"""Tests for exporting a capsule as supplementary materials."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from capsule_corp.export import ExportError, export_capsule, export_html, export_markdown
from capsule_corp.phases.design import design
from capsule_corp.phases.verify import verify
from capsule_corp.settings import Settings
from capsule_corp.store import CapsuleRef, Catalogue
from tests.helpers import ScriptedRunner, _writes


@pytest.fixture
def finished(catalogue: Catalogue) -> CapsuleRef:
    ref = catalogue.create("Bernoulli scaling", question="Does it scale as 1/sqrt(n)?")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    catalogue.freeze(ref)
    (ref.path / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (ref.results_path / "figures").mkdir(parents=True, exist_ok=True)
    (ref.results_path / "figures" / "convergence.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    ref.results_json.write_text(json.dumps({"slope": -0.503, "n_seeds": 50}), encoding="utf-8")
    (ref.path / "REPORT.md").write_text("The scaling held.\n", encoding="utf-8")
    verify(catalogue, ref, None, Settings(), skip_judge=True)
    return ref


def test_markdown_carries_the_claim_worth_making(catalogue: Catalogue, finished: CapsuleRef) -> None:
    """Not 'here are my results' but 'here is what I predicted beforehand'."""
    md = export_markdown(catalogue, finished)
    assert "Pre-registration" in md
    assert "The standard error" in md
    assert "Pre-registration SHA-256" in md
    assert catalogue.prereg_digest(finished)[:16] in md
    assert "slope" in md
    assert "The scaling held." in md


def test_markdown_lists_assumptions(catalogue: Catalogue, finished: CapsuleRef) -> None:
    assert "Assumptions" in export_markdown(catalogue, finished)


def test_markdown_includes_verification_outcome(catalogue: Catalogue, finished: CapsuleRef) -> None:
    md = export_markdown(catalogue, finished)
    assert "Verification" in md
    assert "slope-near-minus-half" in md
    assert "verified" in md


def test_html_is_self_contained(catalogue: Catalogue, finished: CapsuleRef) -> None:
    """It must survive being emailed, so figures are inlined rather than referenced."""
    html = export_html(catalogue, finished)
    assert html.startswith("<!doctype html>")
    assert "data:image/png;base64," in html
    # Every <img> must be inlined. The path may still appear as the criterion of an
    # artifact check, which is correct — that is text, not a resource reference.
    srcs = re.findall(r'<img[^>]*src="([^"]*)"', html)
    assert srcs, "the figure should be rendered"
    assert all(src.startswith("data:") for src in srcs), srcs
    assert "<table>" in html


def test_html_escapes_content(catalogue: Catalogue) -> None:
    ref = catalogue.create("Angle < brackets & ampersands")
    html = export_html(catalogue, ref)
    assert "&lt; brackets &amp; ampersands" in html
    assert "<title>Angle &lt; brackets &amp; ampersands</title>" in html


def test_bundle_contains_the_capsule(catalogue: Catalogue, finished: CapsuleRef, tmp_path: Path) -> None:
    result = export_capsule(catalogue, finished, "bundle", tmp_path / "out.zip")
    with zipfile.ZipFile(result.path) as archive:
        names = archive.namelist()
    prefix = finished.capsule.dirname
    for expected in ("prereg.toml", ".prereg.lock", "run.py", "results/results.json", "EXPORT.md"):
        assert f"{prefix}/{expected}" in names, expected


def test_bundle_excludes_environments(catalogue: Catalogue, finished: CapsuleRef, tmp_path: Path) -> None:
    (finished.path / ".pixi" / "envs").mkdir(parents=True)
    (finished.path / ".pixi" / "envs" / "huge.bin").write_bytes(b"0" * 1000)
    result = export_capsule(catalogue, finished, "bundle", tmp_path / "out.zip")
    with zipfile.ZipFile(result.path) as archive:
        assert not any(".pixi" in name for name in archive.namelist())


def test_default_filename_is_derived(catalogue: Catalogue, finished: CapsuleRef, tmp_path: Path) -> None:
    result = export_capsule(catalogue, finished, "markdown", tmp_path)
    assert result.path.name == f"{finished.capsule.dirname}.md"


def test_unknown_format_rejected(catalogue: Catalogue, finished: CapsuleRef) -> None:
    with pytest.raises(ExportError, match="unknown format"):
        export_capsule(catalogue, finished, "latex")


def test_export_discloses_revisions(catalogue: Catalogue, finished: CapsuleRef) -> None:
    """A revised pre-registration must not look like an unrevised one."""
    catalogue.unfreeze(finished, reason="the tolerance was a typo")
    md = export_markdown(catalogue, finished)
    assert "Pre-registration revisions" in md
    assert "the tolerance was a typo" in md
    assert "1 time(s)" in md


def test_export_flags_revisions_made_after_results_existed(catalogue: Catalogue, finished: CapsuleRef) -> None:
    """The dangerous case gets its own sentence, not a table cell."""
    catalogue.unfreeze(finished, reason="wanted a different threshold")
    md = export_markdown(catalogue, finished)
    assert "after results already existed" in md
    assert "do not carry the evidential" in md


def test_bundle_withholds_agent_transcripts(catalogue: Catalogue, finished: CapsuleRef, tmp_path: Path) -> None:
    """A bundle is meant to be published, and transcripts record everything the agent
    read -- file contents, command output, and paths carrying the author's username."""
    run = finished.runs_path / "20260101T000000-design"
    run.mkdir(parents=True, exist_ok=True)
    (run / "events.jsonl").write_text('{"secret":"/Users/someone/private/notes.txt"}\n', encoding="utf-8")
    (run / "meta.json").write_text('{"phase":"design","ok":true}', encoding="utf-8")

    result = export_capsule(catalogue, finished, "bundle", tmp_path / "out.zip")
    with zipfile.ZipFile(result.path) as archive:
        names = archive.namelist()

    assert not any(n.endswith("events.jsonl") for n in names), "transcripts must not be published"
    # Provenance without content is still useful, so meta.json stays.
    assert any(n.endswith("runs/20260101T000000-design/meta.json") for n in names)


def test_transcripts_can_be_included_deliberately(catalogue: Catalogue, finished: CapsuleRef, tmp_path: Path) -> None:
    run = finished.runs_path / "20260101T000000-design"
    run.mkdir(parents=True, exist_ok=True)
    (run / "events.jsonl").write_text("{}\n", encoding="utf-8")

    result = export_capsule(catalogue, finished, "bundle", tmp_path / "out.zip", include_transcripts=True)
    with zipfile.ZipFile(result.path) as archive:
        assert any(n.endswith("events.jsonl") for n in archive.namelist())
