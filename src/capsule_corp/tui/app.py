"""Textual application for browsing and driving the catalogue.

A thin client over the same library the CLI uses: every action here maps to a function
in :mod:`capsule_corp.phases` or :mod:`capsule_corp.executors`, so the two front ends
cannot drift apart in behaviour.

Long-running phases run in worker threads. They call out to a language model or to a
cluster and would otherwise freeze the interface.
"""

from __future__ import annotations

from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, RichLog, Static, Tree
from textual.widgets.tree import TreeNode

from capsule_corp.executors import get_executor, spec_from_settings
from capsule_corp.models import CapsuleStatus
from capsule_corp.phases.design import design as run_design
from capsule_corp.phases.implement import implement as run_implement
from capsule_corp.phases.verify import verify as run_verify
from capsule_corp.runners import get_runner
from capsule_corp.settings import Settings, global_settings_path, load_settings, project_settings_path
from capsule_corp.store import CapsuleRef, Catalogue
from capsule_corp.ui import status_glyph

EMPTY_DETAIL = "[dim]Select a capsule.[/]"


def _verdict_label(supports: object) -> str:
    """Three-valued on purpose: None means the judge declined to decide."""
    if supports is True:
        return "[green]supports[/]"
    if supports is False:
        return "[magenta]does not support[/]"
    return "[yellow]unclear[/]"


class CapsuleCorpApp(App[None]):
    """Browse capsules, inspect them, and run the phases."""

    TITLE = "capsule-corp"

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #tree { width: 38%; border-right: solid $panel; }
    #right { width: 1fr; }
    #detail { padding: 1 2; height: 1fr; }
    #log { height: 12; border-top: solid $panel; padding: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "run_capsule", "Run"),
        Binding("v", "verify_capsule", "Verify"),
        Binding("d", "design_capsule", "Design"),
        Binding("f", "freeze_capsule", "Freeze"),
        Binding("i", "implement_capsule", "Implement"),
        Binding("s", "show_settings", "Settings"),
        Binding("ctrl+r", "reload", "Reload"),
    ]

    def __init__(self, catalogue: Catalogue) -> None:
        super().__init__()
        self.catalogue = catalogue
        self.settings: Settings = load_settings(catalogue.root)
        self.selected: str | None = None
        self._busy = False

    # ------------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield Tree("capsules", id="tree")
            with Vertical(id="right"):
                with VerticalScroll():
                    yield Static(EMPTY_DETAIL, id="detail", markup=True)
                yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.reload_tree()
        self.log_line(f"[dim]catalogue: {self.catalogue.root}[/]")

    # --------------------------------------------------------------------- tree

    def reload_tree(self) -> None:
        """Rebuild the folder tree from disk."""
        tree = self.query_one("#tree", Tree)
        tree.clear()
        tree.root.expand()

        folders: dict[str, TreeNode[Any]] = {"": tree.root}
        for folder in self.catalogue.folders():
            parent_key, _, name = folder.rpartition("/")
            parent = folders.get(parent_key, tree.root)
            node = parent.add(f"[blue]{name}/[/]", expand=True)
            folders[folder] = node

        count = 0
        for ref in self.catalogue.iter_capsules():
            parent = folders.get(ref.folder, tree.root)
            parent.add_leaf(self._label(ref), data=ref.capsule.id)
            count += 1

        if count == 0:
            tree.root.add_leaf("[dim]no capsules yet[/]")

    @staticmethod
    def _label(ref: CapsuleRef) -> str:
        capsule = ref.capsule
        return f"{status_glyph(capsule.status)} {capsule.id} {capsule.title}"

    @on(Tree.NodeSelected)
    def _node_selected(self, event: Tree.NodeSelected[Any]) -> None:
        ident = event.node.data
        if isinstance(ident, str):
            self.selected = ident
            self.show_detail(ident)

    # ------------------------------------------------------------------- detail

    def show_detail(self, ident: str) -> None:
        ref = self.catalogue.get(ident)
        self.query_one("#detail", Static).update(self.render_detail(ref))

    def render_detail(self, ref: CapsuleRef) -> str:
        """Everything worth seeing about a capsule, as Rich markup."""
        capsule = ref.capsule
        lines = [
            f"[bold]{capsule.title}[/]",
            "",
            f"id        {capsule.id}",
            f"status    {status_glyph(capsule.status)} {capsule.status}",
            f"folder    {ref.folder or '-'}",
            f"frozen    {'yes' if self.catalogue.is_frozen(ref) else 'no'}",
        ]
        provenance = capsule.provenance
        if provenance.model:
            lines.append(f"model     {provenance.provider}/{provenance.model}")
        if provenance.cost_usd:
            lines.append(f"cost      ${provenance.cost_usd:.4f}")
        if capsule.question:
            lines += ["", "[dim]question[/]", capsule.question]

        prereg = self.catalogue.load_prereg(ref)
        if prereg is not None:
            lines += ["", "[dim]hypothesis[/]", prereg.hypothesis]
            if prereg.checks:
                lines += ["", "[dim]registered checks[/]"]
                lines += [f"  • {c.id} [dim]({c.kind})[/]" for c in prereg.checks]

        lines += self._render_verification(ref)
        return "\n".join(lines)

    def _render_verification(self, ref: CapsuleRef) -> list[str]:
        """The checks and the judge's verdict, or nothing if unverified."""
        verification = self._verification(ref)
        if verification is None:
            return []

        lines = ["", "[dim]verification[/]"]
        for check in verification.get("checks", []):
            mark = "[green]✓[/]" if check.get("passed") else "[red]✗[/]"
            lines.append(f"  {mark} {check.get('id')} [dim]{check.get('detail', '')}[/]")

        judge = verification.get("judge")
        if not isinstance(judge, dict):
            return lines

        lines += ["", f"[dim]judge[/]  {_verdict_label(judge.get('supports_hypothesis'))}"]
        if judge.get("reasoning"):
            lines.append(f"  [dim]{judge['reasoning']}[/]")
        return lines

    @staticmethod
    def _verification(ref: CapsuleRef) -> dict[str, Any] | None:
        import json

        if not ref.verification_path.is_file():
            return None
        try:
            loaded: dict[str, Any] = json.loads(ref.verification_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return loaded

    # ---------------------------------------------------------------- utilities

    def log_line(self, message: str) -> None:
        self.query_one("#log", RichLog).write(message)

    def _current(self) -> CapsuleRef | None:
        """The selected capsule, or None with a message explaining why not."""
        if self.selected is None:
            self.log_line("[yellow]select a capsule first[/]")
            return None
        if self._busy:
            self.log_line("[yellow]another phase is still running[/]")
            return None
        return self.catalogue.get(self.selected)

    def _finish(self, message: str) -> None:
        """Called on the UI thread when a worker completes."""
        self._busy = False
        self.log_line(message)
        self.reload_tree()
        if self.selected is not None:
            self.show_detail(self.selected)

    # ------------------------------------------------------------------ actions

    def action_reload(self) -> None:
        self.settings = load_settings(self.catalogue.root)
        self.reload_tree()
        self.log_line("[dim]reloaded[/]")

    def action_freeze_capsule(self) -> None:
        ref = self._current()
        if ref is None:
            return
        try:
            digest = self.catalogue.freeze(ref)
        except Exception as exc:
            self.log_line(f"[red]freeze failed:[/] {exc}")
            return
        self._finish(f"[green]frozen[/] {ref.capsule.id} [dim]{digest[:12]}…[/]")

    def action_design_capsule(self) -> None:
        ref = self._current()
        if ref is None:
            return
        self._busy = True
        self.log_line(f"[cyan]designing[/] {ref.capsule.id} [dim](this calls a model)[/]")
        self._design_worker(ref)

    def action_implement_capsule(self) -> None:
        ref = self._current()
        if ref is None:
            return
        self._busy = True
        self.log_line(f"[cyan]implementing[/] {ref.capsule.id} [dim](this calls a model)[/]")
        self._implement_worker(ref)

    def action_run_capsule(self) -> None:
        ref = self._current()
        if ref is None:
            return
        self._busy = True
        backend = self.settings.executors.default
        self.log_line(f"[cyan]running[/] {ref.capsule.id} [dim](on {backend})[/]")
        self._run_worker(ref, backend)

    def action_verify_capsule(self) -> None:
        ref = self._current()
        if ref is None:
            return
        self._busy = True
        self.log_line(f"[cyan]verifying[/] {ref.capsule.id}")
        self._verify_worker(ref)

    def action_show_settings(self) -> None:
        """Show effective settings and where they are edited."""
        self.query_one("#detail", Static).update(self.render_settings())

    def render_settings(self) -> str:
        """The settings view, as Rich markup."""
        settings = self.settings
        lines = [
            "[bold]settings[/]",
            "",
            f"pixi.channels          {', '.join(settings.pixi.channels)}",
            f"pixi.platforms         {', '.join(settings.pixi.platforms)}",
            f"pixi.default_packages  {', '.join(settings.pixi.default_packages)}",
            f"agent.runner           {settings.agent.runner}",
            f"agent.provider         {settings.agent.provider or 'inherit from pi'}",
            f"agent.model            {settings.agent.model or 'inherit from pi'}",
            f"agent.offline          {settings.agent.offline}",
            f"executors.default      {settings.executors.default}",
            f"executors.slurm.host   {settings.executors.slurm.host or '-'}",
            f"executors.ssh.host     {settings.executors.ssh.host or '-'}",
            f"mcp_servers            {', '.join(s.name for s in settings.enabled_mcp_servers()) or 'none'}",
            "",
            "[dim]edit these files, then press ctrl+r to reload:[/]",
            f"[dim]  global   {global_settings_path()}[/]",
            f"[dim]  project  {project_settings_path(self.catalogue.root)}[/]",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------ workers

    @work(thread=True)
    def _design_worker(self, ref: CapsuleRef) -> None:
        try:
            outcome = run_design(self.catalogue, ref, get_runner(self.settings.agent), self.settings)
            cost = outcome.result.usage.cost_usd
            message = f"[green]designed[/] {ref.capsule.id} [dim]{len(outcome.prereg.checks)} checks, ${cost:.4f}[/]"
        except Exception as exc:
            message = f"[red]design failed:[/] {exc}"
        self.call_from_thread(self._finish, message)

    @work(thread=True)
    def _implement_worker(self, ref: CapsuleRef) -> None:
        try:
            outcome = run_implement(self.catalogue, ref, get_runner(self.settings.agent), self.settings)
            message = f"[green]implemented[/] {ref.capsule.id} [dim]${outcome.result.usage.cost_usd:.4f}[/]"
        except Exception as exc:
            message = f"[red]implementation failed:[/] {exc}"
        self.call_from_thread(self._finish, message)

    @work(thread=True)
    def _run_worker(self, ref: CapsuleRef, backend: str) -> None:
        try:
            executor = get_executor(backend, self.settings)
            usable, reason = executor.available()
            if not usable:
                raise RuntimeError(f"executor {backend!r} is not usable here: {reason}")
            outcome = executor.execute(self.catalogue, ref, self.settings, spec_from_settings(self.settings, backend))
            message = (
                f"[green]ran[/] {ref.capsule.id} [dim]{outcome.duration_seconds:.1f}s[/]"
                if outcome.ok
                else f"[red]run failed:[/] {outcome.error}"
            )
        except Exception as exc:
            message = f"[red]run failed:[/] {exc}"
        self.call_from_thread(self._finish, message)

    @work(thread=True)
    def _verify_worker(self, ref: CapsuleRef) -> None:
        try:
            report = run_verify(self.catalogue, ref, get_runner(self.settings.agent), self.settings)
            passed = sum(1 for c in report.checks if c.passed)
            colour = {
                CapsuleStatus.VERIFIED: "green",
                CapsuleStatus.REFUTED: "magenta",
            }.get(report.status, "red")
            message = f"[{colour}]{report.status}[/] {ref.capsule.id} [dim]{passed}/{len(report.checks)} checks[/]"
        except Exception as exc:
            message = f"[red]verification failed:[/] {exc}"
        self.call_from_thread(self._finish, message)


def launch(catalogue: Catalogue) -> None:
    CapsuleCorpApp(catalogue).run()
