"""Live progress for agent phases.

A design or implementation phase runs for minutes. Without feedback the terminal sits
blank and there is no way to tell a working agent from a hung one, or to see what it
is doing to your capsule. This renders pi's event stream as it arrives.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.status import Status

# Tool names are pi's; the verbs are ours, so the log reads as a narrative.
TOOL_VERBS = {
    "read": "read",
    "write": "wrote",
    "edit": "edited",
    "bash": "ran",
    "list": "listed",
    "glob": "found",
    "grep": "searched",
}

MAX_DETAIL = 72


def _shorten(text: str, limit: int = MAX_DETAIL) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe(tool: str, args: Any) -> str:
    """A one-line summary of a tool call, from whichever argument carries meaning."""
    if not isinstance(args, dict):
        return ""
    for key in ("path", "file_path", "filePath", "command", "pattern", "query"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return _shorten(value)
    return ""


class PhaseReporter:
    """Renders an agent phase's events to the console as they happen."""

    def __init__(self, console: Console, label: str, *, quiet: bool = False) -> None:
        self.console = console
        self.label = label
        self.quiet = quiet
        self._status: Status | None = None
        self._tokens = 0
        self._tools = 0
        self._text = ""

    def __enter__(self) -> PhaseReporter:
        if not self.quiet:
            self._status = self.console.status(self._summary(), spinner="dots")
            self._status.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _summary(self) -> str:
        parts = [f"[cyan]{self.label}[/]"]
        if self._tools:
            parts.append(f"[dim]{self._tools} tool call{'s' if self._tools != 1 else ''}[/]")
        if self._tokens:
            parts.append(f"[dim]{self._tokens:,} tokens[/]")
        return "  ".join(parts)

    def _refresh(self) -> None:
        if self._status is not None:
            self._status.update(self._summary())

    def _log(self, markup: str) -> None:
        """Print above the spinner without disturbing it."""
        if self.quiet:
            return
        self.console.print(markup)

    def handle(self, event: dict[str, Any]) -> None:
        kind = event.get("type")

        if kind == "tool_execution_start":
            tool = str(event.get("toolName") or "tool")
            self._tools += 1
            detail = _describe(tool, event.get("args"))
            verb = TOOL_VERBS.get(tool, tool)
            self._log(f"  [dim]·[/] {verb} [dim]{detail}[/]" if detail else f"  [dim]·[/] {verb}")
            self._refresh()
            return

        if kind == "tool_execution_end" and event.get("isError"):
            self._log(f"  [yellow]![/] [dim]{_shorten(event.get('result', 'tool failed'))}[/]")
            return

        if kind == "message_update":
            usage = event.get("usage")
            if isinstance(usage, dict):
                total = usage.get("totalTokens")
                if isinstance(total, int) and total > self._tokens:
                    self._tokens = total
                    self._refresh()
            self._accumulate(event.get("assistantMessageEvent"))
            return

    def _accumulate(self, message_event: Any) -> None:
        """Collect streamed assistant text so a question or refusal is not silent.

        In non-interactive mode pi cannot pause to ask, so anything the model wants
        to raise arrives as ordinary text. Surfacing it is the only way the user
        finds out that it asked.
        """
        if not isinstance(message_event, dict):
            return
        if message_event.get("type") == "text_delta":
            delta = message_event.get("delta")
            if isinstance(delta, str):
                self._text += delta
        elif message_event.get("type") == "text_end":
            content = message_event.get("content")
            if isinstance(content, str) and content.strip():
                self._text = content

    @property
    def text(self) -> str:
        return self._text.strip()

    def final_note(self) -> None:
        """Show the agent's closing message, which may contain a question or caveat."""
        if self.quiet or not self.text:
            return
        summary = _shorten(self.text, 240)
        self.console.print(f"  [dim]{summary}[/]")
