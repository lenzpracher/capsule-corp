from __future__ import annotations

from pathlib import Path

import pytest

from capsule_corp.store import Catalogue


@pytest.fixture
def catalogue(tmp_path: Path) -> Catalogue:
    return Catalogue.init(tmp_path)


@pytest.fixture
def anyio_backend() -> str:
    """The MCP server is async; run those tests on asyncio only."""
    return "asyncio"
