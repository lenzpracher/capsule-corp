"""Runner backed by ``pi``, an open-source provider-agnostic coding agent.

capsule-corp does not pin a model. It invokes ``pi`` with whatever that tool is already
configured to use, and records provider, model, tokens, and cost from the event stream
so the capsule stays honest about how it was produced.

See https://github.com/earendil-works/pi
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import IO, Any

from capsule_corp.models import Provenance, utcnow
from capsule_corp.runners.base import AgentRequest, AgentResult, Runner, RunnerError, Usage
from capsule_corp.settings import AgentSettings

PI_BINARY = "pi"


class PiRunner:
    """Drives ``pi -p --mode json`` as a subprocess and parses its JSONL events."""

    name = "pi"

    def __init__(self, settings: AgentSettings | None = None, binary: str = PI_BINARY) -> None:
        self.settings = settings or AgentSettings()
        self.binary = binary

    # ------------------------------------------------------------------ invocation

    def _resolve_binary(self) -> str:
        resolved = shutil.which(self.binary)
        if resolved is None:
            raise RunnerError(
                f"{self.binary!r} was not found on PATH. Install it with "
                "'npm install -g @earendil-works/pi-coding-agent', or set agent.runner in settings."
            )
        return resolved

    def version(self) -> str | None:
        try:
            result = subprocess.run(
                [self._resolve_binary(), "--version"], capture_output=True, text=True, timeout=15, check=False
            )
        except (RunnerError, OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None

    def build_argv(self, request: AgentRequest) -> list[str]:
        """Assemble the pi command line.

        ``--approve`` is not optional. pi never prompts for project trust in
        non-interactive modes, and with the default ``defaultProjectTrust = "ask"`` it
        *silently ignores* a project's ``.pi/settings.json``. Without this flag the
        per-capsule agent configuration would appear to do nothing at all.
        """
        argv = [self._resolve_binary(), "-p", "--mode", "json", "--approve"]

        if self.settings.provider:
            argv += ["--provider", self.settings.provider]
        if self.settings.model:
            argv += ["--model", self.settings.model]
        if self.settings.thinking:
            argv += ["--thinking", self.settings.thinking]
        if self.settings.offline:
            argv.append("--offline")

        if request.no_builtin_tools:
            argv.append("--no-builtin-tools")
        if request.tools is not None:
            argv += ["--tools", ",".join(request.tools)]
        if request.system_append:
            argv += ["--append-system-prompt", request.system_append]
        if request.session_id:
            argv += ["--session-id", request.session_id]

        argv.append(request.prompt)
        return argv

    def run(self, request: AgentRequest, events_path: Path | None = None) -> AgentResult:
        argv = self.build_argv(request)
        started = utcnow()

        request.cwd.mkdir(parents=True, exist_ok=True)
        if events_path is not None:
            events_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            process = subprocess.Popen(
                argv,
                cwd=request.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise RunnerError(f"could not start {self.binary}: {exc}") from exc

        collector = _EventCollector()

        sink: IO[str] | nullcontext[None] = (
            events_path.open("w", encoding="utf-8") if events_path is not None else nullcontext()
        )
        with sink as handle, process:
            timed_out = _stream(process, handle, collector, request)
            stderr = process.stderr.read() if process.stderr else ""

        exit_code = process.returncode or 0
        finished = utcnow()

        error: str | None = None
        if timed_out:
            error = f"pi exceeded the {request.timeout_seconds}s timeout and was terminated"
        elif exit_code != 0:
            error = stderr.strip() or f"pi exited with status {exit_code}"
        elif collector.error_message:
            error = collector.error_message

        provenance = Provenance(
            runner=self.name,
            runner_version=self.version(),
            provider=collector.provider or self.settings.provider,
            model=collector.model or self.settings.model,
            thinking=self.settings.thinking,
            started_at=started,
            finished_at=finished,
            total_tokens=collector.usage.total_tokens or None,
            cost_usd=collector.usage.cost_usd or None,
        )

        return AgentResult(
            ok=error is None,
            text=collector.text,
            provenance=provenance,
            usage=collector.usage,
            events_path=events_path,
            exit_code=exit_code,
            error=error,
        )


class _EventCollector:
    """Folds pi's JSONL event stream into the few facts we persist.

    Only ``message_end`` for assistant messages is authoritative: it carries the final
    text along with the provider, model, and cumulative usage actually used.
    """

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.provider: str | None = None
        self.model: str | None = None
        self.api: str | None = None
        self.text: str = ""
        self.usage = Usage()
        self.error_message: str | None = None

    def feed(self, line: str) -> None:
        """Parse a raw stream line and fold it in."""
        event = _parse_line(line)
        if event is not None:
            self.feed_event(event)

    def feed_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "session":
            self.session_id = event.get("id")
            return
        if kind != "message_end":
            return

        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return

        self.provider = message.get("provider") or self.provider
        self.model = message.get("model") or self.model
        self.api = message.get("api") or self.api
        if message.get("stopReason") == "error":
            self.error_message = str(message.get("errorMessage") or "pi reported an error")

        text = "".join(_text_blocks(message.get("content")))
        if text:
            self.text = text
        self.usage = _parse_usage(message.get("usage")) or self.usage


def _stream(
    process: "subprocess.Popen[str]",
    handle: IO[str] | None,
    collector: "_EventCollector",
    request: AgentRequest,
) -> bool:
    """Consume pi's output, tee-ing it and fanning events out. True if it timed out."""
    assert process.stdout is not None
    deadline = time.monotonic() + request.timeout_seconds
    for line in process.stdout:
        if handle is not None:
            handle.write(line)
        event = _parse_line(line)
        if event is not None:
            collector.feed_event(event)
            if request.on_event is not None:
                request.on_event(event)
        if time.monotonic() > deadline:
            process.kill()
            return True
    return False


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse one stream line, tolerating the non-JSON diagnostics pi may interleave."""
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _text_blocks(content: Any) -> Iterator[str]:
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            value = block.get("text")
            if isinstance(value, str):
                yield value


def _parse_usage(raw: Any) -> Usage | None:
    if not isinstance(raw, dict):
        return None
    cost = raw.get("cost")
    total_cost = float(cost.get("total", 0.0)) if isinstance(cost, dict) else 0.0
    return Usage(
        input_tokens=int(raw.get("input", 0) or 0),
        output_tokens=int(raw.get("output", 0) or 0),
        total_tokens=int(raw.get("totalTokens", 0) or 0),
        cost_usd=total_cost,
    )


def get_runner(settings: AgentSettings) -> Runner:
    """Return the runner named in settings."""
    if settings.runner == "pi":
        return PiRunner(settings)
    raise RunnerError(f"unknown runner {settings.runner!r}; the only runner currently built in is 'pi'")
