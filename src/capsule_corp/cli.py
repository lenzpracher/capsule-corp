"""Command-line interface.

All real work lives in the library modules; this file is presentation only, so the
TUI and the MCP server can offer the same operations without going through argv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from typer.core import TyperGroup

from capsule_corp import __version__
from capsule_corp.editor import EditorError, open_in_editor
from capsule_corp.executors import EXECUTOR_NAMES, ExecutorUnavailableError, get_executor, spec_from_settings
from capsule_corp.export import FORMATS, export_capsule
from capsule_corp.index import Index
from capsule_corp.models import CapsuleStatus
from capsule_corp.phases import design as run_design, implement as run_implement, scaffold_capsule
from capsule_corp.phases.design import MAX_ASSUMPTIONS
from capsule_corp.phases.verify import verify as run_verify
from capsule_corp.progress import PhaseReporter
from capsule_corp.runners import RunnerError, Usage, get_runner
from capsule_corp.settings import global_settings_path, load_settings, project_settings_path, save_settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError
from capsule_corp.ui import styled_status as _styled_status

console = Console()
err_console = Console(stderr=True)

# The order `capsule --help` lists commands in. Lifecycle first, in the order you
# actually run them, because that sequence *is* the method: a capsule is designed,
# then frozen, and only then implemented. Alphabetical order would put `verify`
# before `design` and hide that.
CANONICAL_ORDER = (
    # Lifecycle, in running order.
    "new",
    "design",
    "freeze",
    "unfreeze",
    "implement",
    "run",
    "verify",
    # Browsing.
    "list",
    "tree",
    "show",
    "search",
    "open",
    "export",
    # Organising.
    "init",
    "mkdir",
    "mv",
    "rm",
    # Interfaces.
    "tui",
    "mcp",
    # Setup and maintenance.
    "doctor",
    "settings",
    "scaffold",
    "reindex",
    "version",
)

LIFECYCLE_PANEL = "Lifecycle (in order)"
BROWSE_PANEL = "Browsing"
ORGANISE_PANEL = "Organising"
INTERFACE_PANEL = "Interfaces"
SETUP_PANEL = "Setup"


class CanonicalOrderGroup(TyperGroup):
    """Lists commands in CANONICAL_ORDER rather than alphabetically."""

    # ctx is annotated Any because Typer vendors its own click, so the precise
    # Context type lives in the private typer._click module.
    def list_commands(self, ctx: Any) -> list[str]:
        ranks = {name: index for index, name in enumerate(CANONICAL_ORDER)}
        # Anything not listed sorts to the end rather than disappearing.
        return sorted(self.commands, key=lambda name: (ranks.get(name, len(ranks)), name))


app = typer.Typer(
    name="capsule",
    cls=CanonicalOrderGroup,
    help="A terminal catalogue of reproducible, pre-registered research capsules.",
    no_args_is_help=True,
    add_completion=False,
)
settings_app = typer.Typer(help="Inspect and edit preferred tooling and compute settings.", no_args_is_help=True)
app.add_typer(settings_app, name="settings", rich_help_panel=SETUP_PANEL)


def _revision_note(revisions: list[dict[str, Any]]) -> str:
    """Revisions are reported wherever the capsule is, not tucked away in a file."""
    if not revisions:
        return ""
    after_results = sum(1 for r in revisions if r.get("results_existed"))
    detail = f", {after_results} after results existed" if after_results else ""
    return f"  [yellow](revised {len(revisions)}×{detail})[/]"


def _assumption_warning(count: int) -> str:
    """Only worth saying something when the count is high enough to matter."""
    return "  [yellow](this design rests on a lot)[/]" if count > MAX_ASSUMPTIONS else ""


def _assumption_note(count: int) -> str:
    """Flag an assumption-heavy design, since each one narrows what the result covers."""
    if count == 0:
        return ""
    if count > MAX_ASSUMPTIONS:
        return f"[yellow]({count} — this design rests on a lot)[/]"
    return f"[dim]({count})[/]"


def _print_cost(usage: Usage) -> None:
    """Show what an agent phase cost, so a research programme's spend stays visible."""
    if usage.total_tokens:
        console.print(f"  tokens      {usage.total_tokens:,}  [dim](${usage.cost_usd:.4f})[/]")


def _catalogue() -> Catalogue:
    return Catalogue.discover()


@app.command(rich_help_panel=SETUP_PANEL)
def version() -> None:
    """Print the capsule-corp version."""
    console.print(__version__)


@app.command(rich_help_panel=ORGANISE_PANEL)
def init(
    path: Annotated[Path, typer.Argument(help="Where to create the catalogue.")] = Path("."),
) -> None:
    """Create a new capsule catalogue in PATH."""
    catalogue = Catalogue.init(path)
    console.print(f"[green]initialised[/] catalogue at {catalogue.root}")
    console.print(f"  capsules → {catalogue.capsules_root}")


@app.command(rich_help_panel=LIFECYCLE_PANEL)
def new(
    title: Annotated[str, typer.Argument(help="Short title, also used for the slug.")],
    folder: Annotated[str, typer.Option("--folder", "-f", help="Folder to create it in.")] = "",
    question: Annotated[str, typer.Option("--question", "-q", help="The research question.")] = "",
) -> None:
    """Create a new capsule skeleton."""
    catalogue = _catalogue()
    ref = catalogue.create(title=title, folder=folder, question=question)
    console.print(f"[green]created[/] {ref.capsule.dirname}")
    console.print(f"  {ref.path.relative_to(catalogue.root)}")


@app.command(name="list", rich_help_panel=BROWSE_PANEL)
def list_capsules(
    folder: Annotated[str, typer.Option("--folder", "-f", help="Only this folder.")] = "",
    status: Annotated[str, typer.Option("--status", "-s", help="Only this status.")] = "",
) -> None:
    """List capsules in the catalogue."""
    catalogue = _catalogue()
    table = Table(box=None, pad_edge=False)
    table.add_column("id", style="bold")
    table.add_column("title")
    table.add_column("status")
    table.add_column("folder", style="dim")

    count = 0
    for ref in catalogue.iter_capsules():
        if folder and ref.folder != folder:
            continue
        if status and str(ref.capsule.status) != status:
            continue
        table.add_row(ref.capsule.id, ref.capsule.title, _styled_status(ref.capsule.status), ref.folder or "-")
        count += 1

    if count == 0:
        console.print("[dim]no capsules yet — create one with 'capsule new \"<question>\"'[/]")
        return
    console.print(table)


@app.command(rich_help_panel=BROWSE_PANEL)
def tree() -> None:
    """Show the catalogue as a folder tree."""
    catalogue = _catalogue()
    root = Tree(f"[bold]{catalogue.capsules_root.name}[/]")
    nodes: dict[str, Tree] = {"": root}

    for folder in catalogue.folders():
        parent_key, _, name = folder.rpartition("/")
        parent = nodes.get(parent_key, root)
        nodes[folder] = parent.add(f"[blue]{name}/[/]")

    for ref in catalogue.iter_capsules():
        parent = nodes.get(ref.folder, root)
        parent.add(f"{ref.capsule.id} {ref.capsule.title} {_styled_status(ref.capsule.status)}")

    console.print(root)


@app.command(rich_help_panel=BROWSE_PANEL)
def show(ident: Annotated[str, typer.Argument(help="Capsule id, slug, or directory name.")]) -> None:
    """Show a capsule's manifest and pre-registration."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    capsule = ref.capsule

    body = [
        f"[bold]{capsule.title}[/]",
        "",
        f"id        {capsule.id}",
        f"status    {_styled_status(capsule.status)}",
        f"folder    {ref.folder or '-'}",
        f"tags      {', '.join(capsule.tags) or '-'}",
        f"frozen    {'yes' if catalogue.is_frozen(ref) else 'no'}{_revision_note(catalogue.revisions(ref))}",
        f"path      {ref.path.relative_to(catalogue.root)}",
    ]
    if capsule.question:
        body += ["", "[dim]question[/]", capsule.question]

    prereg = catalogue.load_prereg(ref)
    if prereg is not None:
        body += ["", "[dim]hypothesis[/]", prereg.hypothesis]
        if prereg.predictions:
            body += ["", "[dim]predictions[/]"]
            body += [f"  • {p}" for p in prereg.predictions]
        # Assumptions are shown unconditionally, including when there are none:
        # "assumes nothing extra" is itself worth knowing, and a long list is a
        # signal the result covers less than it appears to.
        body += ["", f"[dim]assumptions[/] {_assumption_note(len(prereg.assumptions))}"]
        body += [f"  • {a}" for a in prereg.assumptions] or ["  [dim]none recorded[/]"]
        if prereg.checks:
            body += ["", "[dim]checks[/]"]
            body += [f"  • {c.id} ({c.kind})" for c in prereg.checks]

    console.print(Panel("\n".join(body), border_style="cyan", expand=False))


@app.command(rich_help_panel=ORGANISE_PANEL)
def mkdir(path: Annotated[str, typer.Argument(help="Folder path within the catalogue.")]) -> None:
    """Create a folder in the catalogue."""
    catalogue = _catalogue()
    created = catalogue.make_folder(path)
    console.print(f"[green]created[/] {created.relative_to(catalogue.root)}")


@app.command(rich_help_panel=ORGANISE_PANEL)
def mv(
    ident: Annotated[str, typer.Argument(help="Capsule to move.")],
    dest: Annotated[str, typer.Argument(help="Destination folder.")],
) -> None:
    """Move a capsule into another folder."""
    catalogue = _catalogue()
    ref = catalogue.move(ident, dest)
    console.print(f"[green]moved[/] {ref.capsule.dirname} → {ref.folder or '/'}")


@app.command(rich_help_panel=ORGANISE_PANEL)
def rm(
    ident: Annotated[str, typer.Argument(help="Capsule to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Delete a capsule and everything in it."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    if not yes:
        typer.confirm(f"Delete {ref.capsule.dirname} and all its results?", abort=True)
    removed = catalogue.remove(ident)
    console.print(f"[red]deleted[/] {removed.name}")


@app.command(rich_help_panel=LIFECYCLE_PANEL)
def freeze(ident: Annotated[str, typer.Argument(help="Capsule to freeze.")]) -> None:
    """Lock a capsule's pre-registration so its predictions can no longer change."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    digest = catalogue.freeze(ref)
    console.print(f"[green]frozen[/] {ref.capsule.dirname}")
    console.print(f"  sha256 {digest[:16]}…")
    console.print("[dim]  predictions and checks are now fixed; implementation can begin[/]")


@app.command(rich_help_panel=LIFECYCLE_PANEL)
def design(
    ident: Annotated[str, typer.Argument(help="Capsule to design.")],
    note: Annotated[str, typer.Option("--note", "-n", help="Extra constraints for the designer.")] = "",
    fresh: Annotated[bool, typer.Option("--fresh", help="Start over instead of revising the existing design.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Hide live progress.")] = False,
) -> None:
    """Write or revise the pre-registration, before any code exists."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    settings = load_settings(catalogue.root)

    try:
        runner = get_runner(settings.agent)
    except RunnerError as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[cyan]designing[/] {ref.capsule.dirname} [dim](runner: {settings.agent.runner})[/]")
    with PhaseReporter(console, "designing", quiet=quiet) as reporter:
        outcome = run_design(catalogue, ref, runner, settings, extra_instructions=note, on_event=reporter.handle)
    reporter.final_note()

    provenance = outcome.result.provenance
    console.print(f"[green]{'revised' if outcome.revised else 'designed'}[/] {ref.capsule.dirname}")
    console.print(f"  hypothesis  {outcome.prereg.hypothesis}")
    console.print(
        f"  assumptions {len(outcome.prereg.assumptions)}{_assumption_warning(len(outcome.prereg.assumptions))}"
    )
    console.print(f"  checks      {len(outcome.prereg.checks)}")
    console.print(f"  model       {provenance.provider}/{provenance.model}")
    _print_cost(outcome.result.usage)
    console.print(
        f"[dim]  review prereg.toml — edit it, or run 'capsule design {ref.capsule.id} -n \"...\"' again —[/]"
    )
    console.print(f"[dim]  then lock it with 'capsule freeze {ref.capsule.id}'[/]")


@app.command(rich_help_panel=LIFECYCLE_PANEL)
def implement(
    ident: Annotated[str, typer.Argument(help="Capsule to implement.")],
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Hide live progress.")] = False,
) -> None:
    """Write the code for a frozen capsule."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    settings = load_settings(catalogue.root)

    try:
        runner = get_runner(settings.agent)
    except RunnerError as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[cyan]implementing[/] {ref.capsule.dirname}")
    with PhaseReporter(console, "implementing", quiet=quiet) as reporter:
        outcome = run_implement(catalogue, ref, runner, settings, on_event=reporter.handle)
    reporter.final_note()

    provenance = outcome.result.provenance
    console.print(f"[green]implemented[/] {ref.capsule.dirname}")
    console.print(f"  model       {provenance.provider}/{provenance.model}")
    _print_cost(outcome.result.usage)
    console.print("[dim]  pre-registration hash verified before and after[/]")


@app.command(rich_help_panel=SETUP_PANEL)
def scaffold(
    ident: Annotated[str, typer.Argument(help="Capsule to scaffold.")],
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Replace existing files.")] = False,
) -> None:
    """Write the per-capsule pixi and agent configuration."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    written = scaffold_capsule(ref, load_settings(catalogue.root), overwrite=overwrite)
    if not written:
        console.print("[dim]nothing to write; pass --overwrite to replace existing files[/]")
        return
    for name in written:
        console.print(f"[green]wrote[/] {name}")


@app.command(rich_help_panel=LIFECYCLE_PANEL)
def unfreeze(
    ident: Annotated[str, typer.Argument(help="Capsule to unfreeze.")],
    reason: Annotated[str, typer.Option("--reason", "-r", help="Why. Recorded permanently.")] = "",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Release a frozen pre-registration so it can be revised.

    The superseded version and your reason are archived with the capsule and reported
    in every view and export, because a prediction revised after the fact is not the
    same evidence as one that was not.
    """
    catalogue = _catalogue()
    ref = catalogue.get(ident)

    if not catalogue.is_frozen(ref):
        err_console.print(f"[yellow]capsule {ref.capsule.id} is not frozen[/]")
        raise typer.Exit(1)

    if not reason:
        reason = typer.prompt("Reason for unfreezing (recorded permanently)")

    has_results = ref.results_json.is_file()
    if has_results:
        console.print(
            "[yellow]warning:[/] this capsule already has results. Revising a prediction "
            "after seeing the outcome is the thing pre-registration exists to prevent."
        )
        console.print("[dim]  the record will note that results existed at the time.[/]")
    if not yes:
        typer.confirm(f"Unfreeze {ref.capsule.dirname}?", abort=True)

    archive = catalogue.unfreeze(ref, reason)
    console.print(f"[yellow]unfrozen[/] {ref.capsule.dirname}")
    console.print(f"  superseded version archived to {archive.relative_to(catalogue.root)}")
    if has_results:
        console.print("[dim]  the previous verification was moved into the archive; re-verify after revising[/]")


@app.command(rich_help_panel=LIFECYCLE_PANEL)
def run(
    ident: Annotated[str, typer.Argument(help="Capsule to run.")],
    on: Annotated[str, typer.Option("--on", help=f"Where to run it: {', '.join(EXECUTOR_NAMES)}.")] = "",
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds before the run is killed.")] = 0,
) -> None:
    """Execute a capsule's experiment."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    settings = load_settings(catalogue.root)

    backend = on or settings.executors.default
    try:
        executor = get_executor(backend, settings)
    except Exception as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from exc

    usable, reason = executor.available()
    if not usable:
        err_console.print(f"[red]error:[/] executor {backend!r} is not usable here: {reason}")
        raise typer.Exit(1)

    console.print(f"[cyan]running[/] {ref.capsule.dirname} [dim](on {backend})[/]")
    spec = spec_from_settings(settings, backend, timeout_seconds=timeout or None)
    try:
        outcome = executor.execute(catalogue, ref, settings, spec)
    except ExecutorUnavailableError as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from exc

    if not outcome.ok:
        err_console.print(f"[red]failed:[/] {outcome.error}")
        err_console.print(f"[dim]  output: {outcome.stdout_path.relative_to(catalogue.root)}[/]")
        raise typer.Exit(1)

    job = f" [dim](job {outcome.job_id})[/]" if outcome.job_id else ""
    console.print(f"[green]ran[/] {ref.capsule.dirname} in {outcome.duration_seconds:.1f}s{job}")
    if ref.results_json.is_file():
        console.print(f"  results     {ref.results_json.relative_to(catalogue.root)}")
    else:
        console.print("[yellow]  warning:[/] no results/results.json was produced")


@app.command(rich_help_panel=LIFECYCLE_PANEL)
def verify(
    ident: Annotated[str, typer.Argument(help="Capsule to verify.")],
    strict: Annotated[bool, typer.Option("--strict", help="Let the judge's verdict change the outcome.")] = False,
    no_judge: Annotated[bool, typer.Option("--no-judge", help="Run the deterministic checks only.")] = False,
) -> None:
    """Evaluate the pre-registered checks, then run the blinded judge."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    settings = load_settings(catalogue.root)

    runner = None
    if not no_judge:
        try:
            runner = get_runner(settings.agent)
        except RunnerError as exc:
            err_console.print(f"[red]error:[/] {exc}")
            raise typer.Exit(1) from exc

    console.print(f"[cyan]verifying[/] {ref.capsule.dirname}")
    with PhaseReporter(console, "verifying", quiet=no_judge) as reporter:
        report = run_verify(
            catalogue, ref, runner, settings, strict=strict, skip_judge=no_judge, on_event=reporter.handle
        )

    for check in report.checks:
        if check.error:
            console.print(f"  [red]![/] {check.id} [dim]{check.error}[/]")
        elif check.passed:
            console.print(f"  [green]✓[/] {check.id} [dim]{check.detail}[/]")
        else:
            console.print(f"  [red]✗[/] {check.id} [dim]{check.detail}[/]")

    passed = sum(1 for c in report.checks if c.passed)
    console.print(f"  [dim]{passed}/{len(report.checks)} checks passed[/]")

    if report.judge is not None:
        verdict = {True: "[green]supports[/]", False: "[magenta]does not support[/]", None: "[yellow]unclear[/]"}[
            report.judge.supports_hypothesis
        ]
        confidence = f" (confidence {report.judge.confidence:.2f})" if report.judge.confidence is not None else ""
        console.print(f"\n  judge       {verdict}{confidence}")
        if report.judge.reasoning:
            console.print(f"  [dim]{report.judge.reasoning}[/]")
        for concern in report.judge.concerns:
            console.print(f"  [yellow]·[/] [dim]{concern}[/]")

    console.print(f"\n[bold]{ref.capsule.id}[/] → {_styled_status(report.status)}")
    if report.status is CapsuleStatus.REFUTED:
        console.print("[dim]  the hypothesis was not supported; this is a completed capsule, not a failed one[/]")


@app.command(rich_help_panel=INTERFACE_PANEL)
def tui() -> None:
    """Open the interactive terminal interface."""
    from capsule_corp.tui import CapsuleCorpApp

    CapsuleCorpApp(_catalogue()).run()


@app.command(rich_help_panel=INTERFACE_PANEL)
def mcp() -> None:
    """Serve the catalogue over MCP on stdio, for any MCP client to drive."""
    from capsule_corp.mcp_server import serve

    serve(_catalogue().root)


@app.command(rich_help_panel=SETUP_PANEL)
def doctor() -> None:
    """Check that everything capsule-corp depends on is present and configured."""
    from capsule_corp.doctor import run_checks

    try:
        root: Path | None = _catalogue().root
    except CatalogueError:
        root = None
    checks = run_checks(load_settings(root))

    table = Table(box=None, pad_edge=False)
    table.add_column("")
    table.add_column("check", style="bold")
    table.add_column("detail")
    for check in checks:
        mark = "[green]OK[/]" if check.ok else "[yellow]--[/]"
        detail = check.detail + (f"  [dim]{check.advice}[/]" if check.advice else "")
        table.add_row(mark, check.name, detail)
    console.print(table)

    blocking = [c for c in checks if not c.ok and not c.name.startswith("executor:") and c.name != "pi mcp adapter"]
    if blocking:
        console.print(f"\n[yellow]{len(blocking)} issue(s) would stop capsules from being produced.[/]")
        raise typer.Exit(1)


@app.command(rich_help_panel=SETUP_PANEL)
def reindex() -> None:
    """Rebuild the search index from the files on disk."""
    catalogue = _catalogue()
    count = Index.for_catalogue(catalogue).rebuild(catalogue)
    console.print(f"[green]indexed[/] {count} capsule(s)")


@app.command(rich_help_panel=BROWSE_PANEL)
def open(
    ident: Annotated[str, typer.Argument(help="Capsule to open.")],
    file: Annotated[str, typer.Option("--file", "-f", help="Open one file instead of the directory.")] = "",
    editor: Annotated[str, typer.Option("--editor", "-e", help="Editor command, e.g. code.")] = "",
) -> None:
    """Open a capsule in your editor (VS Code by default)."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    settings = load_settings(catalogue.root)

    target = ref.path / file if file else ref.path
    if not target.exists():
        err_console.print(f"[red]error:[/] {target.relative_to(catalogue.root)} does not exist")
        raise typer.Exit(1)

    try:
        command = open_in_editor(target, settings.editor, editor or None)
    except EditorError as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]opened[/] {target.name} [dim]in {command}[/]")


@app.command(rich_help_panel=BROWSE_PANEL)
def export(
    ident: Annotated[str, typer.Argument(help="Capsule to export.")],
    fmt: Annotated[str, typer.Option("--format", help=f"One of: {', '.join(FORMATS)}.")] = "bundle",
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Destination file or directory.")] = None,
) -> None:
    """Export a capsule for attaching to a paper.

    `bundle` is a zip of supplementary materials, `markdown` an appendix, and `html`
    a single self-contained file with the figures inlined.
    """
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    result = export_capsule(catalogue, ref, fmt, out)

    console.print(f"[green]exported[/] {ref.capsule.dirname} [dim]as {result.format}[/]")
    console.print(f"  {result.path}  [dim]{result.bytes_written:,} bytes[/]")

    revisions = catalogue.revisions(ref)
    if revisions:
        console.print(
            f"[yellow]note:[/] this capsule's pre-registration was revised {len(revisions)} time(s) "
            "after freezing; the export says so."
        )


@app.command(rich_help_panel=BROWSE_PANEL)
def search(
    query: Annotated[str, typer.Argument(help="Full-text query.")],
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
) -> None:
    """Search titles, questions, and reports."""
    catalogue = _catalogue()
    index = Index.for_catalogue(catalogue)
    try:
        hits = index.search(query, limit=limit)
    except ValueError as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise typer.Exit(1) from exc

    if not hits:
        console.print("[dim]no matches[/]")
        return
    for hit in hits:
        console.print(f"[bold]{hit.id}[/] {hit.title} [dim]{hit.folder}[/] [{hit.status}]")
        if hit.snippet.strip():
            console.print(f"  [dim]{hit.snippet.strip()}[/]")


@settings_app.command("show")
def settings_show() -> None:
    """Print the effective settings."""
    try:
        root: Path | None = _catalogue().root
    except CatalogueError:
        root = None
    settings = load_settings(root)

    table = Table(box=None, pad_edge=False)
    table.add_column("setting", style="bold")
    table.add_column("value")
    table.add_row("pixi.channels", ", ".join(settings.pixi.channels))
    table.add_row("pixi.platforms", ", ".join(settings.pixi.platforms))
    table.add_row("pixi.default_packages", ", ".join(settings.pixi.default_packages))
    table.add_row("agent.runner", settings.agent.runner)
    table.add_row("agent.provider", settings.agent.provider or "[dim]inherit from pi[/]")
    table.add_row("agent.model", settings.agent.model or "[dim]inherit from pi[/]")
    table.add_row("agent.offline", str(settings.agent.offline))
    table.add_row("executors.default", settings.executors.default)
    table.add_row("mcp_servers", ", ".join(s.name for s in settings.enabled_mcp_servers()) or "[dim]none[/]")
    console.print(table)


@settings_app.command("path")
def settings_path() -> None:
    """Show where settings are read from."""
    console.print(f"global   {global_settings_path()}")
    try:
        console.print(f"project  {project_settings_path(_catalogue().root)}")
    except CatalogueError:
        console.print("[dim]project  (not inside a catalogue)[/]")


@settings_app.command("init")
def settings_init(
    project: Annotated[
        bool, typer.Option("--project", help="Write to the catalogue instead of the user config.")
    ] = False,
) -> None:
    """Write a settings file populated with the current defaults."""
    target = project_settings_path(_catalogue().root) if project else global_settings_path()
    if target.is_file():
        err_console.print(f"[yellow]exists:[/] {target}")
        raise typer.Exit(1)
    written = save_settings(load_settings(), target)
    console.print(f"[green]wrote[/] {written}")


def main() -> None:
    """Entry point that turns catalogue errors into clean messages."""
    try:
        app()
    except CatalogueError as exc:
        err_console.print(f"[red]error:[/] {exc}")
        raise SystemExit(1) from exc


__all__ = ["app", "main", "CapsuleRef"]
