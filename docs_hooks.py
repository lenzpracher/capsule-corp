"""MkDocs build hooks.

Publishes install.sh alongside the site so the documented one-liner resolves:

    curl -fsSL https://lenzpracher.github.io/capsule-corp/install.sh | sh

Kept as a hook rather than a shell step in the build task so it happens however the
site is built, including under `mkdocs serve`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def on_post_build(config: Any, **_: Any) -> None:
    source = Path(config["config_file_path"]).parent / "install.sh"
    if not source.is_file():
        raise FileNotFoundError(f"{source} is missing; the documented install URL would 404")
    destination = Path(config["site_dir"]) / "install.sh"
    shutil.copy2(source, destination)
    destination.chmod(0o755)
