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

Early. Milestone 1 (catalogue, storage format, CLI) is implemented. The agent loop,
verification, TUI, remote execution, and MCP server are in progress — see
[`docs/roadmap.md`](docs/roadmap.md).

## Install

Requires [pixi](https://pixi.sh).

```bash
git clone https://github.com/lenzpracher/capsule-corp
cd capsule-corp
pixi install && pixi run postinstall
```

## Use

```bash
capsule init ~/research          # create a catalogue
capsule mkdir optimization       # organise it however you like
capsule new "Does LR warmup lower final loss?" --folder optimization
capsule freeze 0001              # lock the pre-registration
capsule list                     # or: capsule tree
capsule search warmup
```

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

## License

MIT
