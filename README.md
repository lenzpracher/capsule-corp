<p align="center">
  <img src="docs/assets/logo.svg" alt="capsule-corp" width="440">
</p>

# capsule-corp

Packaging reproducible research questions.

A terminal catalogue where each **capsule** is one empirically testable research question,
packaged so that it stays reproducible and honest: the question, a frozen pre-registration,
the code, a pinned environment, the outputs, and a verification record.

Capsules are primarily written by an LLM. Every artifact is plain text and editable by hand.

## Why pre-registration

The failure mode of LLM-generated research is not bad code — it is code that quietly gets
rewritten until it agrees with the conclusion. capsule-corp splits a capsule into two phases
with a lock in between:

1. **Design.** An agent writes the question, the hypothesis, and a set of _machine-checkable_
   assertions that would falsify it — before any implementation exists.
2. **Freeze.** `prereg.toml` is hashed into `.prereg.lock` and committed. From here the
   predictions and the checks cannot change.
3. **Implement, run, verify.** A second agent writes the code. The hash is re-verified before
   and after; if the pre-registration moved, the run fails.

Verification then has two independent layers: the deterministic checks (no LLM involved, so
they are a real gate), and a **blinded** LLM judge that sees the question, the code, and the
outputs — but not the write-up or any of the author's claims.

A capsule whose hypothesis is refuted is a _successful_ capsule. That outcome is recorded,
not treated as a failure.

## Status

Early but complete end to end: catalogue, agent loop, pre-registered verification, TUI,
remote execution, and the MCP server are all implemented. The Slurm, SSH and Modal
backends are unit-tested but have not yet been pointed at real infrastructure — see
[`docs/roadmap.md`](docs/roadmap.md).

## Install

```bash
curl -fsSL https://lenzpracher.github.io/capsule-corp/install.sh | sh
```

This puts the `capsule` command in `~/.local/bin` using [uv](https://docs.astral.sh/uv/),
in its own isolated environment. No sudo. As with any installer of this shape, read
[the script](install.sh) before piping it to a shell.

Or, equivalently:

```bash
uv tool install git+https://github.com/lenzpracher/capsule-corp
```

To hack on capsule-corp itself, clone it and install editable so the command tracks
your working copy:

```bash
git clone https://github.com/lenzpracher/capsule-corp
cd capsule-corp
uv tool install --editable .
pixi install && pixi run postinstall   # for the test and lint tasks
```

Then run `capsule doctor`. Two external tools do the real work:
[pi](https://github.com/earendil-works/pi) writes the capsules, and
[pixi](https://pixi.sh) manages each capsule's environment.

## Use

```bash
capsule doctor                   # check pi, pixi and the compute backends
capsule init ~/research          # create a catalogue
capsule mkdir optimization       # organise it however you like

capsule new "Does LR warmup lower final loss?" --folder optimization
capsule design 0001              # write the pre-registration; re-run to revise it
capsule freeze 0001              # lock it; predictions can no longer change
capsule implement 0001           # write the code
capsule run 0001 --on slurm      # local (default), slurm, ssh, or modal
capsule verify 0001              # checks, then the blinded judge

capsule open 0001                # read the code in VS Code
capsule export 0001              # a supplementary-materials bundle for a paper
capsule tui                      # browse and drive it interactively
capsule mcp                      # serve the catalogue to any MCP client
capsule search warmup
```

Commands are listed in `capsule --help` in the order you run them, grouped by purpose,
because that sequence is the method rather than an implementation detail.

## Interface

`capsule tui` opens a Textual interface over the same library the CLI uses: a folder
tree, a detail pane showing the pre-registration and the verdict, a settings view, and
`r`un / `v`erify / `d`esign / `f`reeze / `i`mplement keybinds. Press `e` for a built-in
file browser and editor with syntax highlighting, or `o` to open the capsule in
VS Code. Phases run in worker threads, so the interface stays responsive while a model
is working.

Long phases stream their progress as they run — each file the agent writes, each
command it runs, a live token count — so you can tell a working agent from a hung one.

## Attaching capsules to papers

```bash
capsule export 0001                     # supplementary-materials zip
capsule export 0001 --format markdown   # an appendix
capsule export 0001 --format html       # one self-contained file
```

Every export carries the pre-registration hash and the provenance, and discloses any
revision the registration went through after being frozen. See
[`docs/export.md`](docs/export.md).

## Running elsewhere

Capsules run locally by default, and unchanged on Slurm, any SSH host with Docker, or
Modal. See [`docs/compute.md`](docs/compute.md).

## Driving it from an MCP client

`capsule mcp` serves the catalogue over the Model Context Protocol, including
`capsule://<id>` resources so an agent can read prior capsules as context. See
[`docs/mcp.md`](docs/mcp.md).

## A capsule on disk

```
capsules/optimization/0001-lr-warmup/
  capsule.toml         manifest: id, status, provenance
  QUESTION.md          the research question
  prereg.toml          hypothesis, predictions, checks
  .prereg.lock         sha256 of prereg.toml at freeze time
  AGENTS.md            instructions for the implementing agent
  .pi/settings.json    per-capsule pinned agent config
  pixi.toml            per-capsule environment
  src/  run.py         the implementation
  results/             results.json + figures/
  runs/<timestamp>/    transcript and provenance for each run
  verification.json    check results + judge verdict
  REPORT.md            the write-up
```

Files are the source of truth. The SQLite index is a cache and can be rebuilt at any time
with `capsule reindex`.

## Agent independence

The coding agent is [`pi`](https://github.com/earendil-works/pi), which is open source and
provider-agnostic — configure it for Claude, GPT, Gemini, or a local model. capsule-corp does
not pin a model; it inherits whatever `pi` is configured with and records what was actually
used in each capsule's manifest. The runner sits behind a protocol, so other agents can be
plugged in.

## Documentation

<https://lenzpracher.github.io/capsule-corp>

## License

MIT
