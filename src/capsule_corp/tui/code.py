"""In-TUI file browser and editor for a capsule.

Reading the generated code is part of using this tool, not an afterthought: a capsule
you cannot inspect is automated, not reproducible. This keeps that one keystroke away
rather than requiring you to leave for an editor.

Frozen files are read-only here, enforced on save rather than by hiding them — you
should still be able to *read* the pre-registration you are being held to.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DirectoryTree, Footer, Static, TextArea

from capsule_corp.store import PREREG, PREREG_LOCK, CapsuleRef

# Files whose contents are fixed by the freeze. Editing them would either break the
# hash or, worse, quietly succeed before anyone re-checked it.
FROZEN_FILES = {PREREG, PREREG_LOCK}

# Directories not worth browsing: environments and caches, plus raw agent transcripts.
HIDDEN_DIRS = {".pixi", "__pycache__", ".git", "runs"}

LANGUAGES = {
    ".py": "python",
    ".toml": "toml",
    ".json": "json",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "bash",
}

MAX_BYTES = 512_000


class CapsuleDirectoryTree(DirectoryTree):
    """A directory tree that hides environments, caches and transcripts."""

    def filter_paths(self, paths):  # type: ignore[no-untyped-def]
        return [p for p in paths if p.name not in HIDDEN_DIRS and not p.name.endswith(".pyc")]


class CodeScreen(ModalScreen[None]):
    """Browse and edit the files of one capsule."""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+o", "open_external", "Open in editor"),
    ]

    CSS = """
    CodeScreen { align: center middle; }
    #frame { width: 92%; height: 90%; background: $surface; border: thick $primary; }
    #tree { width: 32%; border-right: solid $panel; }
    #editor { width: 1fr; }
    #banner { height: 1; padding: 0 1; background: $panel; color: $text; }
    """

    def __init__(self, ref: CapsuleRef, frozen: bool) -> None:
        super().__init__()
        self.ref = ref
        self.frozen = frozen
        self.current: Path | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="frame"):
            yield Static(self._banner_text(), id="banner", markup=True)
            with Horizontal():
                yield CapsuleDirectoryTree(str(self.ref.path), id="tree")
                yield TextArea("", id="editor", read_only=True, show_line_numbers=True)
            yield Footer()

    def _banner_text(self, message: str = "") -> str:
        if message:
            return message
        name = self.current.name if self.current else "select a file"
        state = ""
        if self.current is not None:
            state = " [yellow](read-only: frozen)[/]" if self._is_locked(self.current) else " [dim](ctrl+s to save)[/]"
        return f"[bold]{self.ref.capsule.dirname}[/] — {name}{state}"

    def _is_locked(self, path: Path) -> bool:
        return self.frozen and path.name in FROZEN_FILES

    @on(DirectoryTree.FileSelected)
    def _file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.load_file(event.path)

    def load_file(self, path: Path) -> None:
        editor = self.query_one("#editor", TextArea)
        try:
            if path.stat().st_size > MAX_BYTES:
                editor.load_text(f"[{path.name} is too large to show here: {path.stat().st_size:,} bytes]")
                editor.read_only = True
                self.current = None
                self._refresh_banner()
                return
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            editor.load_text(f"[cannot display {path.name}: {exc}]")
            editor.read_only = True
            self.current = None
            self._refresh_banner()
            return

        self.current = path
        editor.load_text(text)
        language = LANGUAGES.get(path.suffix.lower())
        try:
            editor.language = language
        except Exception:
            # An unavailable tree-sitter grammar must not stop the file displaying.
            editor.language = None
        editor.read_only = self._is_locked(path)
        self._refresh_banner()

    def _refresh_banner(self, message: str = "") -> None:
        self.query_one("#banner", Static).update(self._banner_text(message))

    def action_save(self) -> None:
        if self.current is None:
            self._refresh_banner("[yellow]no file selected[/]")
            return
        if self._is_locked(self.current):
            self._refresh_banner(
                f"[red]{self.current.name} is frozen and cannot be edited.[/] "
                "[dim]Create a new capsule for a revised prediction.[/]"
            )
            return
        try:
            self.current.write_text(self.query_one("#editor", TextArea).text, encoding="utf-8")
        except OSError as exc:
            self._refresh_banner(f"[red]could not save: {exc}[/]")
            return
        self._refresh_banner(f"[green]saved[/] {self.current.name}")

    def action_open_external(self) -> None:
        """Hand the capsule to the configured editor, e.g. VS Code."""
        from capsule_corp.editor import EditorError, open_in_editor
        from capsule_corp.settings import load_settings

        target = self.current or self.ref.path
        try:
            command = open_in_editor(target, load_settings().editor)
        except EditorError as exc:
            self._refresh_banner(f"[red]{exc}[/]")
            return
        self._refresh_banner(f"[green]opened[/] {target.name} [dim]in {command}[/]")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
