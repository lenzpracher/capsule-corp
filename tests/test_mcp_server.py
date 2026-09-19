"""Tests for the MCP server surface.

The point of this layer is that any MCP client can drive the catalogue, so the tests
go through the registered tools rather than calling the library directly.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from capsule_corp.mcp_server import build_server
from capsule_corp.phases.design import design
from capsule_corp.settings import Settings
from capsule_corp.store import Catalogue
from tests.helpers import ScriptedRunner, _writes


@pytest.fixture
def server(catalogue: Catalogue):  # type: ignore[no-untyped-def]
    return build_server(catalogue.root)


async def _call(server, name: str, **kwargs):  # type: ignore[no-untyped-def]
    """Invoke a registered tool the way a client would, returning its structured result.

    MCPServer wraps a list return in {"result": [...]}, so unwrap that for readability.
    """
    result = await server.call_tool(name, kwargs)
    if result.is_error:
        raise RuntimeError(result.content[0].text if result.content else "tool call failed")
    payload = result.structured_content
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


@pytest.mark.anyio
async def test_lists_registered_tools(server) -> None:  # type: ignore[no-untyped-def]
    names = {tool.name for tool in await server.list_tools()}
    assert {
        "capsule_list",
        "capsule_search",
        "capsule_get",
        "capsule_new",
        "capsule_design",
        "capsule_freeze",
        "capsule_implement",
        "capsule_run",
        "capsule_verify",
    } <= names


@pytest.mark.anyio
async def test_new_then_list(server, catalogue: Catalogue) -> None:  # type: ignore[no-untyped-def]
    await _call(server, "capsule_new", title="Warmup study", question="Does warmup help?")
    rows = await _call(server, "capsule_list")
    assert len(rows) == 1
    assert rows[0]["title"] == "Warmup study"
    assert rows[0]["frozen"] is False


@pytest.mark.anyio
async def test_get_returns_the_prereg_and_results(server, catalogue: Catalogue) -> None:  # type: ignore[no-untyped-def]
    ref = catalogue.create("Bernoulli", question="Does it converge?")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    ref.results_json.parent.mkdir(parents=True, exist_ok=True)
    ref.results_json.write_text(json.dumps({"slope": -0.5, "n_seeds": 50}), encoding="utf-8")

    detail = await _call(server, "capsule_get", ident="0001")
    assert detail["prereg"]["hypothesis"].startswith("The standard error")
    assert [c["id"] for c in detail["prereg"]["checks"]] == ["slope-near-minus-half", "figure"]
    assert detail["results"]["n_seeds"] == 50


@pytest.mark.anyio
async def test_freeze_through_mcp_is_recorded(server, catalogue: Catalogue) -> None:  # type: ignore[no-untyped-def]
    ref = catalogue.create("Freezable")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    frozen = await _call(server, "capsule_freeze", ident="0001")
    assert len(frozen["sha256"]) == 64
    assert catalogue.is_frozen(catalogue.get("0001"))


@pytest.mark.anyio
async def test_search_finds_by_question(server, catalogue: Catalogue) -> None:  # type: ignore[no-untyped-def]
    catalogue.create("Warmup", question="Does learning rate warmup reduce loss?")
    catalogue.create("Tokenizer", question="Something about vocabularies.")
    rows = await _call(server, "capsule_search", query="warmup")
    assert [h["id"] for h in rows] == ["0001"]


@pytest.mark.anyio
async def test_capsule_resource_is_exposed(server) -> None:  # type: ignore[no-untyped-def]
    templates = await server.list_resource_templates()
    assert any("capsule://" in str(t.uri_template) for t in templates)


@pytest.mark.anyio
async def test_index_resource_lists_everything(server, catalogue: Catalogue) -> None:  # type: ignore[no-untyped-def]
    catalogue.create("One")
    catalogue.create("Two")
    contents = await server.read_resource("capsule://index")
    payload = json.loads(list(contents)[0].content)
    assert {row["title"] for row in payload} == {"One", "Two"}


@pytest.mark.anyio
async def test_unknown_capsule_reports_why(server) -> None:  # type: ignore[no-untyped-def]
    """The client must be told what went wrong so an agent can correct itself.

    Catalogue errors are converted to ToolError; an unconverted exception would be
    treated as a server crash and its message withheld.
    """
    with pytest.raises(ToolError, match="no capsule matching 'nope'"):
        await _call(server, "capsule_get", ident="nope")


@pytest.mark.anyio
async def test_freezing_twice_reports_why(server, catalogue: Catalogue) -> None:  # type: ignore[no-untyped-def]
    ref = catalogue.create("Once")
    design(catalogue, ref, ScriptedRunner(_writes()), Settings())
    await _call(server, "capsule_freeze", ident="0001")
    with pytest.raises(ToolError):
        await _call(server, "capsule_design", ident="0001")
