"""capsule-corp as an MCP server.

Exposes the catalogue over the Model Context Protocol so any MCP client — Claude Code,
Claude Desktop, Cursor, Zed, or an agent you wrote yourself — can drive it. capsule-corp
is deliberately not tied to one vendor's agent, and this is the other half of that: not
just which model writes a capsule, but which assistant can read the catalogue.

The resources matter as much as the tools. Exposing capsules as ``capsule://<id>`` lets
an agent ask "what have I already tested here, and what did it show?" before spending
compute on a question that is already answered.

Written against the mcp 2.x API, where FastMCP was renamed to MCPServer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from capsule_corp import __version__
from capsule_corp.executors import get_executor, spec_from_settings
from capsule_corp.index import Index
from capsule_corp.models import CapsuleStatus
from capsule_corp.phases.design import design as run_design
from capsule_corp.phases.implement import implement as run_implement
from capsule_corp.phases.verify import verify as run_verify
from capsule_corp.runners import get_runner
from capsule_corp.settings import load_settings
from capsule_corp.store import CapsuleRef, Catalogue, CatalogueError

INSTRUCTIONS = """\
capsule-corp is a catalogue of pre-registered research capsules. Each capsule is one
empirically testable question, with its hypothesis and falsification criteria frozen
before any code was written.

Before proposing a new experiment, search the catalogue: a question may already have
been answered, and a refuted hypothesis is as informative as a confirmed one. Read
prior capsules with the capsule://<id> resources.

Freezing is not reversible by design. If a registered prediction turns out to be wrong,
create a new capsule rather than trying to amend the old one.
"""


def _summarize(catalogue: Catalogue, ref: CapsuleRef) -> dict[str, Any]:
    capsule = ref.capsule
    return {
        "id": capsule.id,
        "title": capsule.title,
        "question": capsule.question,
        "status": str(capsule.status),
        "folder": ref.folder,
        "tags": capsule.tags,
        "frozen": catalogue.is_frozen(ref),
        "updated_at": capsule.updated_at.isoformat(),
    }


def _detail(catalogue: Catalogue, ref: CapsuleRef) -> dict[str, Any]:
    """Everything an agent needs to understand a capsule without reading the tree."""
    detail = _summarize(catalogue, ref)

    prereg = catalogue.load_prereg(ref)
    if prereg is not None:
        detail["prereg"] = {
            "hypothesis": prereg.hypothesis,
            "predictions": prereg.predictions,
            "analysis_plan": prereg.analysis_plan,
            "results_contract": prereg.results_contract,
            "checks": [{"id": c.id, "kind": str(c.kind), "description": c.description} for c in prereg.checks],
        }
    if ref.results_json.is_file():
        try:
            detail["results"] = json.loads(ref.results_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            detail["results"] = None
    if ref.verification_path.is_file():
        try:
            detail["verification"] = json.loads(ref.verification_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            detail["verification"] = None

    report = ref.path / "REPORT.md"
    if report.is_file():
        detail["report"] = report.read_text(encoding="utf-8")
    detail["provenance"] = ref.capsule.provenance.model_dump(mode="json", exclude_none=True)
    return detail


_F = TypeVar("_F", bound=Callable[..., Any])


def _reporting(fn: _F) -> _F:
    """Turn catalogue errors into MCP tool errors.

    Without this, an ordinary mistake like a bad capsule id is treated as a server
    crash and its message is withheld from the client, so the agent is told only that
    "the tool failed" and cannot correct itself.
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except CatalogueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper  # type: ignore[return-value]


def build_server(root: Path | None = None) -> MCPServer:
    """Construct the MCP server for the catalogue rooted at ``root``."""
    server = MCPServer(name="capsule-corp", version=__version__, instructions=INSTRUCTIONS)

    def catalogue() -> Catalogue:
        return Catalogue(root) if root is not None else Catalogue.discover()

    _register_read_tools(server, catalogue)
    _register_lifecycle_tools(server, catalogue)
    _register_resources(server, catalogue)
    return server


def _tool_factory(server: MCPServer) -> Callable[..., Callable[[_F], _F]]:
    """Register tools through the catalogue-error reporter."""

    def tool(**kwargs: Any) -> Callable[[_F], _F]:
        def register(fn: _F) -> _F:
            registered: _F = server.tool(**kwargs)(_reporting(fn))
            return registered

        return register

    return tool


def _register_read_tools(server: MCPServer, catalogue: Callable[[], Catalogue]) -> None:
    """Tools that only read. Safe for an agent to call freely."""
    tool = _tool_factory(server)

    @tool(description="List capsules in the catalogue, optionally filtered by folder or status.")
    def capsule_list(folder: str = "", status: str = "") -> list[dict[str, Any]]:
        cat = catalogue()
        return [
            _summarize(cat, ref)
            for ref in cat.iter_capsules()
            if (not folder or ref.folder == folder) and (not status or str(ref.capsule.status) == status)
        ]

    @tool(description="Full-text search over capsule titles, questions, and reports.")
    def capsule_search(query: str, limit: int = 20) -> list[dict[str, Any]]:
        cat = catalogue()
        index = Index.for_catalogue(cat)
        index.rebuild(cat)
        return [
            {"id": h.id, "title": h.title, "folder": h.folder, "status": h.status, "snippet": h.snippet}
            for h in index.search(query, limit=limit)
        ]

    @tool(description="Get one capsule in full: pre-registration, results, verification, and report.")
    def capsule_get(ident: str) -> dict[str, Any]:
        cat = catalogue()
        return _detail(cat, cat.get(ident))


def _register_lifecycle_tools(server: MCPServer, catalogue: Callable[[], Catalogue]) -> None:
    """Tools that create, spend money, or change state irreversibly."""
    tool = _tool_factory(server)

    @tool(description="Create a new capsule skeleton. Does not run the designer.")
    def capsule_new(title: str, question: str = "", folder: str = "") -> dict[str, Any]:
        cat = catalogue()
        return _summarize(cat, cat.create(title=title, folder=folder, question=question))

    @tool(
        description=(
            "Run the design phase: write the question and the pre-registration. This spends money on an LLM call."
        )
    )
    def capsule_design(ident: str, note: str = "") -> dict[str, Any]:
        cat = catalogue()
        ref = cat.get(ident)
        settings = load_settings(cat.root)
        outcome = run_design(cat, ref, get_runner(settings.agent), settings, extra_instructions=note)
        return {
            "id": ref.capsule.id,
            "hypothesis": outcome.prereg.hypothesis,
            "checks": [c.id for c in outcome.prereg.checks],
            "cost_usd": outcome.result.usage.cost_usd,
        }

    @tool(
        description=(
            "Freeze a capsule's pre-registration. Irreversible: after this its predictions and checks cannot change."
        )
    )
    def capsule_freeze(ident: str) -> dict[str, Any]:
        cat = catalogue()
        ref = cat.get(ident)
        return {"id": ref.capsule.id, "sha256": cat.freeze(ref)}

    @tool(description="Run the implementation phase for a frozen capsule. This spends money on an LLM call.")
    def capsule_implement(ident: str) -> dict[str, Any]:
        cat = catalogue()
        ref = cat.get(ident)
        settings = load_settings(cat.root)
        outcome = run_implement(cat, ref, get_runner(settings.agent), settings)
        return {"id": ref.capsule.id, "ok": outcome.result.ok, "cost_usd": outcome.result.usage.cost_usd}

    @tool(description="Execute a capsule's experiment on the named backend (local, slurm, ssh, modal).")
    def capsule_run(ident: str, on: str = "") -> dict[str, Any]:
        cat = catalogue()
        ref = cat.get(ident)
        settings = load_settings(cat.root)
        backend = on or settings.executors.default
        executor = get_executor(backend, settings)
        outcome = executor.execute(cat, ref, settings, spec_from_settings(settings, backend))
        return {
            "id": ref.capsule.id,
            "ok": outcome.ok,
            "executor": outcome.executor,
            "duration_seconds": round(outcome.duration_seconds, 2),
            "error": outcome.error,
        }

    @tool(
        description=(
            "Verify a capsule: evaluate the pre-registered deterministic checks, then run "
            "the blinded judge unless skip_judge is set."
        )
    )
    def capsule_verify(ident: str, strict: bool = False, skip_judge: bool = False) -> dict[str, Any]:
        cat = catalogue()
        ref = cat.get(ident)
        settings = load_settings(cat.root)
        runner = None if skip_judge else get_runner(settings.agent)
        report = run_verify(cat, ref, runner, settings, strict=strict, skip_judge=skip_judge)
        parsed: dict[str, Any] = json.loads(report.model_dump_json(exclude_none=True))
        return parsed


def _register_resources(server: MCPServer, catalogue: Callable[[], Catalogue]) -> None:
    """Capsules as readable context, so an agent can check what is already known."""

    @server.resource(
        "capsule://{ident}",
        description="A single capsule: question, pre-registration, results, verdict, and report.",
        mime_type="application/json",
    )
    def capsule_resource(ident: str) -> str:
        cat = catalogue()
        return json.dumps(_detail(cat, cat.get(ident)), indent=2)

    @server.resource(
        "capsule://index",
        name="catalogue index",
        description="Every capsule in the catalogue with its current status.",
        mime_type="application/json",
    )
    def index_resource() -> str:
        cat = catalogue()
        return json.dumps([_summarize(cat, ref) for ref in cat.iter_capsules()], indent=2)


def serve(root: Path | None = None) -> None:
    """Run the MCP server over stdio."""
    build_server(root).run(transport="stdio")


__all__ = ["CapsuleStatus", "CatalogueError", "build_server", "serve"]
