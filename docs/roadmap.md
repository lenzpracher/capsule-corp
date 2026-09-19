# Roadmap

Each milestone is independently useful and reviewable.

## M1 — Foundation ✅

Catalogue storage format, freeze/tamper detection, settings, SQLite index, and the CLI
(`init`, `new`, `list`, `tree`, `show`, `mkdir`, `mv`, `rm`, `freeze`, `reindex`, `search`,
`settings`).

## M2 — Agent loop

`Runner` protocol and `PiRunner`, which shells out to `pi -p --mode json` and parses the JSONL
event stream. Design phase (writes `QUESTION.md` + `prereg.toml`) and implement phase (writes
code, with the pre-registration hash verified before and after). Per-capsule `.pi/settings.json`
and `pixi.toml` generation.

> **Note for implementers:** `pi` does not prompt for project trust in non-interactive modes
> (`-p`, `--mode json`, `--mode rpc`). With the default `defaultProjectTrust = "ask"` it
> _silently ignores_ a project's `.pi/settings.json`. `PiRunner` must pass `--approve`, or
> per-capsule agent config will appear to do nothing.

## M3 — Verification

The check evaluator (restricted AST, no `eval`), the blinded judge, `verification.json`, and
`REPORT.md`. Blinding is enforced by copying only the permitted files into the judge's
workspace, not by asking the model nicely.

## M4 — TUI

Textual application over the same library API: folder tree, capsule detail pane, settings
screen, run/verify keybinds, and a live view of the run event stream.

## M5 — Compute

`Executor` protocol. `local` first, then `slurm` (sbatch + rsync + poll — note that cluster
compute nodes usually have no outbound network, so `pi` needs `--offline`) and `ssh_docker`,
which share a transport layer, then `modal`.

## M6 — MCP

- capsule-corp **as an MCP server** (`capsule mcp`, stdio): `capsule_list`, `capsule_search`,
  `capsule_get`, `capsule_new`, `capsule_run`, `capsule_verify`. Any MCP client can drive the
  catalogue.
- Capsules **as MCP resources** (`capsule://<id>`), so an agent can read prior capsules as
  context — "what have I already tested here, and what did it show?"
- capsule-corp **as an MCP client**: capsule runs get tools from MCP servers declared in
  settings, pinned per capsule.

`pi` has no built-in MCP support; it is provided by the third-party `pi-mcp-adapter`
extension. `capsule doctor` should install and configure a pinned version. Track
[earendil-works/pi#563](https://github.com/earendil-works/pi/issues/563) for an official path.
