"""Command-line interface.

All real work lives in the library modules; this file is presentation only, so the
TUI and the MCP server can offer the same operations without going through argv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from capsule_corp import __version__
from capsule_corp.index import Index
from capsule_corp.models import CapsuleStatus
from capsule_corp.settings import global_settings_path, load_settings, project_settings_path, save_settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="capsule",
    help="A terminal catalogue of reproducible, pre-registered research capsules.",
    no_args_is_help=True,
    add_completion=False,
)
settings_app = typer.Typer(help="Inspect and edit preferred tooling and compute settings.", no_args_is_help=True)
app.add_typer(settings_app, name="settings")

STATUS_STYLE: dict[CapsuleStatus, str] = {
    CapsuleStatus.DRAFT: "dim",
    CapsuleStatus.DESIGNED: "cyan",
    CapsuleStatus.FROZEN: "yellow",
    CapsuleStatus.IMPLEMENTED: "blue",
    CapsuleStatus.RUN: "blue",
    CapsuleStatus.VERIFIED: "green",
    CapsuleStatus.REFUTED: "magenta",
    CapsuleStatus.FAILED: "red",
}


def _styled_status(status: CapsuleStatus) -> str:
    return f"[{STATUS_STYLE[status]}]{status}[/]"


def _catalogue() -> Catalogue:
    return Catalogue.discover()


@app.command()
def version() -> None:
    """Print the capsule-corp version."""
    console.print(__version__)


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Where to create the catalogue.")] = Path("."),
) -> None:
    """Create a new capsule catalogue in PATH."""
    catalogue = Catalogue.init(path)
    console.print(f"[green]initialised[/] catalogue at {catalogue.root}")
    console.print(f"  capsules → {catalogue.capsules_root}")


@app.command()
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


@app.command(name="list")
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


@app.command()
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


@app.command()
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
        f"frozen    {'yes' if catalogue.is_frozen(ref) else 'no'}",
        f"path      {ref.path.relative_to(catalogue.root)}",
    ]
    if capsule.question:
        body += ["", "[dim]question[/]", capsule.question]

    prereg = catalogue.load_prereg(ref)
    if prereg is not None:
        body += ["", "[dim]hypothesis[/]", prereg.hypothesis]
        if prereg.checks:
            body += ["", "[dim]checks[/]"]
            body += [f"  • {c.id} ({c.kind})" for c in prereg.checks]

    console.print(Panel("\n".join(body), border_style="cyan", expand=False))


@app.command()
def mkdir(path: Annotated[str, typer.Argument(help="Folder path within the catalogue.")]) -> None:
    """Create a folder in the catalogue."""
    catalogue = _catalogue()
    created = catalogue.make_folder(path)
    console.print(f"[green]created[/] {created.relative_to(catalogue.root)}")


@app.command()
def mv(
    ident: Annotated[str, typer.Argument(help="Capsule to move.")],
    dest: Annotated[str, typer.Argument(help="Destination folder.")],
) -> None:
    """Move a capsule into another folder."""
    catalogue = _catalogue()
    ref = catalogue.move(ident, dest)
    console.print(f"[green]moved[/] {ref.capsule.dirname} → {ref.folder or '/'}")


@app.command()
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


@app.command()
def freeze(ident: Annotated[str, typer.Argument(help="Capsule to freeze.")]) -> None:
    """Lock a capsule's pre-registration so its predictions can no longer change."""
    catalogue = _catalogue()
    ref = catalogue.get(ident)
    digest = catalogue.freeze(ref)
    console.print(f"[green]frozen[/] {ref.capsule.dirname}")
    console.print(f"  sha256 {digest[:16]}…")
    console.print("[dim]  predictions and checks are now fixed; implementation can begin[/]")


@app.command()
def reindex() -> None:
    """Rebuild the search index from the files on disk."""
    catalogue = _catalogue()
    count = Index.for_catalogue(catalogue).rebuild(catalogue)
    console.print(f"[green]indexed[/] {count} capsule(s)")


@app.command()
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
