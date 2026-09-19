from __future__ import annotations

from pathlib import Path

import pytest

from capsule_corp.store import Catalogue


@pytest.fixture
def catalogue(tmp_path: Path) -> Catalogue:
    return Catalogue.init(tmp_path)
