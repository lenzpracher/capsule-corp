"""Opening a capsule in an external editor.

Reading the generated code matters as much as running it: a capsule you cannot
inspect is not reproducible in any useful sense, it is just automated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from capsule_corp.settings import EditorSettings
from capsule_corp.store import CatalogueError

# Editors that block the terminal until the file is closed. Launching one from the
# TUI would deadlock the interface, so callers are told to suspend first.
TERMINAL_EDITORS = {"vim", "nvim", "vi", "nano", "emacs", "helix", "hx", "micro", "kak"}


class EditorError(CatalogueError):
    pass


def resolve_editor(settings: EditorSettings, override: str | None = None) -> str:
    """Pick an editor command: explicit override, then settings, then $VISUAL/$EDITOR."""
    for candidate in (override, settings.command, os.environ.get("VISUAL"), os.environ.get("EDITOR")):
        if candidate:
            if shutil.which(candidate.split()[0]) is None:
                raise EditorError(f"editor {candidate!r} was not found on PATH")
            return candidate

    for candidate in settings.candidates:
        if shutil.which(candidate) is not None:
            return candidate

    raise EditorError(
        "no editor found. Set $EDITOR, or editor.command in settings, or install one of: "
        + ", ".join(settings.candidates)
    )


def is_terminal_editor(command: str) -> bool:
    return Path(command.split()[0]).name in TERMINAL_EDITORS


def open_in_editor(target: Path, settings: EditorSettings, override: str | None = None) -> str:
    """Open ``target`` and return the command used.

    Graphical editors are detached so the shell returns immediately; terminal editors
    are run in the foreground so they actually get the terminal.
    """
    command = resolve_editor(settings, override)
    argv = [*command.split(), str(target)]

    try:
        if is_terminal_editor(command):
            subprocess.run(argv, check=False)
        else:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise EditorError(f"could not launch {command!r}: {exc}") from exc
    return command
