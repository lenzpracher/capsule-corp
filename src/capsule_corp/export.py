"""Exporting a capsule for publication.

A capsule is only useful as evidence if someone else can receive it. These formats
are for attaching to a paper: a supplementary-materials archive, a Markdown appendix,
and a single self-contained HTML file that survives being emailed around.

Every format carries the pre-registration hash and the provenance, because the claim
worth making about a capsule is not "here are my results" but "here is what I
predicted before I looked, and here is what happened".
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from capsule_corp.models import Prereg, Verification
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError

FORMATS = ("bundle", "markdown", "html")

# Excluded from the archive: environments are machine-specific and enormous, and raw
# agent transcripts are large and rarely what a reader wants. pixi.lock stays, since
# that is what actually makes the environment reproducible.
BUNDLE_EXCLUDES = (".pixi", "__pycache__", ".DS_Store")

_LIST_ITEM = re.compile(r"^(- |\d+\. )")
_ORDERED = re.compile(r"^\d+\. ")
_IMAGE = re.compile(r"!\[(.*?)\]\((.*?)\)")

FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ExportError(CatalogueError):
    pass


@dataclass
class ExportResult:
    path: Path
    format: str
    bytes_written: int


def _figures(ref: CapsuleRef) -> list[Path]:
    figures_dir = ref.results_path / "figures"
    if not figures_dir.is_dir():
        return []
    return sorted(p for p in figures_dir.iterdir() if p.suffix.lower() in FIGURE_SUFFIXES)


def _results(ref: CapsuleRef) -> dict[str, Any] | None:
    if not ref.results_json.is_file():
        return None
    try:
        loaded: dict[str, Any] = json.loads(ref.results_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded


def _verification(ref: CapsuleRef) -> Verification | None:
    if not ref.verification_path.is_file():
        return None
    try:
        return Verification.model_validate_json(ref.verification_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _digest(catalogue: Catalogue, ref: CapsuleRef) -> str:
    try:
        return catalogue.prereg_digest(ref)
    except CatalogueError:
        return ""


def _is_excluded(path: Path, root: Path) -> bool:
    return any(part in BUNDLE_EXCLUDES for part in path.relative_to(root).parts)


def export_bundle(catalogue: Catalogue, ref: CapsuleRef, destination: Path) -> ExportResult:
    """Zip the capsule as supplementary materials."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    prefix = ref.capsule.dirname

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ref.path.rglob("*")):
            if path.is_dir() or _is_excluded(path, ref.path):
                continue
            archive.write(path, arcname=f"{prefix}/{path.relative_to(ref.path)}")
        # A reader who unzips this should not have to install anything to know what
        # they are looking at.
        archive.writestr(f"{prefix}/EXPORT.md", export_markdown(catalogue, ref, embed_figures=False))

    return ExportResult(path=destination, format="bundle", bytes_written=destination.stat().st_size)


def _provenance_rows(catalogue: Catalogue, ref: CapsuleRef) -> list[tuple[str, str]]:
    provenance = ref.capsule.provenance
    digest = _digest(catalogue, ref)
    rows = [
        ("Capsule", ref.capsule.dirname),
        ("Status", str(ref.capsule.status)),
        ("Pre-registration SHA-256", digest or "not frozen"),
    ]
    if provenance.model:
        rows.append(("Model", f"{provenance.provider or '?'}/{provenance.model}"))
    if provenance.runner_version:
        rows.append(("Runner", f"{provenance.runner} {provenance.runner_version}"))
    if provenance.executor:
        rows.append(("Executor", provenance.executor))
    if provenance.git_sha:
        rows.append(("Git commit", provenance.git_sha))
    rows.append(("Exported", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")))
    return rows


def export_markdown(catalogue: Catalogue, ref: CapsuleRef, *, embed_figures: bool = False) -> str:
    """Render the capsule as a Markdown appendix."""
    capsule = ref.capsule
    prereg = catalogue.load_prereg(ref)
    verification = _verification(ref)
    results = _results(ref)

    out: list[str] = [f"# {capsule.title}", ""]
    if capsule.question:
        out += [capsule.question, ""]

    out += ["## Provenance", ""]
    out += ["| Field | Value |", "| --- | --- |"]
    out += [f"| {name} | `{value}` |" for name, value in _provenance_rows(catalogue, ref)]
    out += [""]

    revisions = catalogue.revisions(ref)
    if revisions:
        out += _revisions_markdown(revisions)

    if prereg is not None:
        out += _prereg_markdown(prereg)

    if results is not None:
        out += ["## Results", "", "```json", json.dumps(results, indent=2), "```", ""]

    figures = _figures(ref)
    if figures:
        out += ["## Figures", ""]
        for figure in figures:
            if embed_figures:
                out += [f"![{figure.stem}]({_data_uri(figure)})", ""]
            else:
                out += [f"![{figure.stem}](results/figures/{figure.name})", ""]

    if verification is not None:
        out += _verification_markdown(verification)

    report = ref.path / "REPORT.md"
    if report.is_file():
        out += ["## Report", "", report.read_text(encoding="utf-8").strip(), ""]

    return "\n".join(out).rstrip() + "\n"


def _revisions_markdown(revisions: list[dict[str, Any]]) -> list[str]:
    """Disclose that the pre-registration was revised after being frozen.

    Reported prominently rather than in a footnote: it materially changes how much
    weight the registration carries.
    """
    after_results = sum(1 for r in revisions if r.get("results_existed"))
    out = [
        "## Pre-registration revisions",
        "",
        f"This pre-registration was unfrozen and revised **{len(revisions)} time(s)** after it was",
        "first locked. The superseded versions are retained in the capsule under",
        "`.prereg-history/`.",
        "",
    ]
    if after_results:
        out += [
            f"**{after_results} of these revisions happened after results already existed.**",
            "Predictions changed after an outcome was visible do not carry the evidential",
            "weight of predictions registered beforehand.",
            "",
        ]
    out += ["| When | Reason | Results existed |", "| --- | --- | --- |"]
    for record in revisions:
        out.append(
            f"| {record.get('unfrozen_at', '?')} | {record.get('reason', '')} | "
            f"{'yes' if record.get('results_existed') else 'no'} |"
        )
    out += [""]
    return out


def _prereg_markdown(prereg: Prereg) -> list[str]:
    out = ["## Pre-registration", "", f"**Hypothesis.** {prereg.hypothesis}", ""]
    if prereg.predictions:
        out += ["**Predictions registered in advance.**", ""]
        out += [f"{i}. {p}" for i, p in enumerate(prereg.predictions, 1)]
        out += [""]
    out += ["**Assumptions.**", ""]
    out += [f"- {a}" for a in prereg.assumptions] or ["- None recorded."]
    out += [""]
    if prereg.analysis_plan:
        out += ["**Analysis plan.**", "", prereg.analysis_plan, ""]
    if prereg.checks:
        out += ["**Registered checks.**", "", "| Check | Kind | Criterion |", "| --- | --- | --- |"]
        for check in prereg.checks:
            criterion = check.expr or check.path or check.script or ""
            out.append(f"| `{check.id}` | {check.kind} | `{criterion}` |")
        out += [""]
    return out


def _verification_markdown(verification: Verification) -> list[str]:
    out = ["## Verification", "", "| Check | Outcome | Detail |", "| --- | --- | --- |"]
    for check in verification.checks:
        outcome = "pass" if check.passed else "FAIL"
        out.append(f"| `{check.id}` | {outcome} | {check.detail or check.error or ''} |")
    out += ["", f"**Outcome.** `{verification.status}`", ""]

    judge = verification.judge
    if judge is not None:
        verdict = (
            "supports"
            if judge.supports_hypothesis is True
            else "does not support"
            if judge.supports_hypothesis is False
            else "cannot determine"
        )
        confidence = f" (confidence {judge.confidence:.2f})" if judge.confidence is not None else ""
        out += [
            "**Blinded judge.** An independent model reviewed the question, code and outputs",
            "without access to the write-up.",
            "",
            f"> Verdict: {verdict}{confidence}.",
            "",
        ]
        if judge.reasoning:
            out += [f"> {judge.reasoning}", ""]
        for concern in judge.concerns:
            out += [f"> - {concern}"]
        if judge.concerns:
            out += [""]
    return out


def _data_uri(path: Path) -> str:
    mime = MIME.get(path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ max-width: 46rem; margin: 3rem auto; padding: 0 1.25rem;
         font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         color: #1a1a1a; background: #fff; }}
  h1 {{ font-size: 1.75rem; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 2.25rem; border-bottom: 1px solid #e5e5e5;
        padding-bottom: 0.3rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ededed;
            vertical-align: top; }}
  th {{ font-weight: 600; color: #555; }}
  code {{ font: 0.86em ui-monospace, SFMono-Regular, Menlo, monospace;
          background: #f4f4f5; padding: 0.1em 0.35em; border-radius: 3px; }}
  pre {{ background: #f7f7f8; padding: 0.9rem; border-radius: 6px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; }}
  blockquote {{ margin: 1rem 0; padding: 0.6rem 1rem; border-left: 3px solid #1d63b4;
                background: #f6f9fd; color: #333; }}
  img {{ max-width: 100%; border: 1px solid #e5e5e5; border-radius: 4px; }}
  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e5e5e5;
            font-size: 0.8rem; color: #777; }}
  @media print {{ body {{ margin: 0; max-width: none; }} }}
</style>
</head>
<body>
{body}
<footer>
Generated by <a href="https://lenzpracher.github.io/capsule-corp">capsule-corp</a>.
The pre-registration hash above fixes the predictions and checks as they stood before
the implementation was written.
</footer>
</body>
</html>
"""


def export_html(catalogue: Catalogue, ref: CapsuleRef) -> str:
    """Render a single self-contained HTML file, figures inlined."""
    markdown = export_markdown(catalogue, ref, embed_figures=True)
    return HTML_TEMPLATE.format(title=_escape(ref.capsule.title), body=_markdown_to_html(markdown))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _HtmlRenderer:
    """A deliberately small Markdown-subset renderer.

    Only what export_markdown itself emits: headings, tables, fenced code, images,
    blockquotes, lists, bold, and inline code. Pulling in a full Markdown dependency
    to render output we generate ourselves is not worth the dependency.
    """

    def __init__(self) -> None:
        self.out: list[str] = []
        self.in_code = False
        self.in_table = False
        self.in_list = False

    def render(self, markdown: str) -> str:
        for line in markdown.splitlines():
            self._line(line)
        self._close_blocks()
        if self.in_code:
            self.out.append("</code></pre>")
        return "\n".join(self.out)

    def _close_blocks(self) -> None:
        if self.in_table:
            self.out.append("</table>")
            self.in_table = False
        if self.in_list:
            self.out.append("</ul>")
            self.in_list = False

    def _line(self, line: str) -> None:
        if line.startswith("```"):
            self._close_blocks()
            self.out.append("</code></pre>" if self.in_code else "<pre><code>")
            self.in_code = not self.in_code
            return
        if self.in_code:
            self.out.append(_escape(line))
            return

        stripped = line.strip()
        if not stripped:
            self._close_blocks()
            return

        for matches, handle in (
            (stripped.startswith(("# ", "## ")), self._heading),
            (stripped.startswith("!["), self._image),
            (stripped.startswith("|"), self._table_row),
            (stripped.startswith("> "), self._quote),
            (bool(_LIST_ITEM.match(stripped)), self._list_item),
        ):
            if matches:
                handle(stripped)
                return
        self._paragraph(stripped)

    def _heading(self, text: str) -> None:
        self._close_blocks()
        level, _, content = text.partition(" ")
        tag = "h2" if len(level) == 2 else "h1"
        self.out.append(f"<{tag}>{_inline(content)}</{tag}>")

    def _image(self, text: str) -> None:
        self._close_blocks()
        match = _IMAGE.match(text)
        if match:
            self.out.append(f'<p><img alt="{_escape(match.group(1))}" src="{match.group(2)}"></p>')

    def _table_row(self, text: str) -> None:
        cells = [c.strip() for c in text.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            return  # the |---| separator row
        if not self.in_table:
            self.out.append("<table>")
            self.in_table = True
            self.out.append("<tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells) + "</tr>")
        else:
            self.out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")

    def _quote(self, text: str) -> None:
        self._close_blocks()
        self.out.append(f"<blockquote>{_inline(text[2:])}</blockquote>")

    def _list_item(self, text: str) -> None:
        if self.in_table:
            self._close_blocks()
        if not self.in_list:
            self.out.append("<ul>")
            self.in_list = True
        content = text[2:] if text.startswith("- ") else _ORDERED.sub("", text)
        self.out.append(f"<li>{_inline(content)}</li>")

    def _paragraph(self, text: str) -> None:
        self._close_blocks()
        self.out.append(f"<p>{_inline(text)}</p>")


def _markdown_to_html(markdown: str) -> str:
    return _HtmlRenderer().render(markdown)


def _inline(text: str) -> str:
    escaped = _escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def export_capsule(catalogue: Catalogue, ref: CapsuleRef, fmt: str, destination: Path | None = None) -> ExportResult:
    """Export a capsule in ``fmt``, returning where it landed."""
    if fmt not in FORMATS:
        raise ExportError(f"unknown format {fmt!r}; available: {', '.join(FORMATS)}")

    suffix = {"bundle": ".zip", "markdown": ".md", "html": ".html"}[fmt]
    target = destination or Path.cwd() / f"{ref.capsule.dirname}{suffix}"
    if target.is_dir():
        target = target / f"{ref.capsule.dirname}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "bundle":
        return export_bundle(catalogue, ref, target)

    content = export_markdown(catalogue, ref) if fmt == "markdown" else export_html(catalogue, ref)
    target.write_text(content, encoding="utf-8")
    return ExportResult(path=target, format=fmt, bytes_written=target.stat().st_size)


__all__ = ["FORMATS", "ExportError", "ExportResult", "export_capsule", "export_html", "export_markdown", "shutil"]
