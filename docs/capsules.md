# Capsules

A capsule is one empirically testable research question, packaged as a directory of
plain text. Files are the source of truth: the SQLite index is a cache that
`capsule reindex` rebuilds from disk, so a corrupted database is never a lost capsule.

```
capsules/optimization/0001-lr-warmup/
  capsule.toml         manifest: id, status, provenance
  QUESTION.md          the research question and design, in prose
  prereg.toml          hypothesis, predictions, results contract, checks
  .prereg.lock         sha256 of prereg.toml, plus when it was frozen
  AGENTS.md            instructions for the implementing agent
  .pi/settings.json    per-capsule pinned agent config
  pixi.toml            per-capsule environment
  run.py  src/         the implementation
  results/             results.json + figures/ + stdout.log
  runs/<timestamp>/    events.jsonl, meta.json for each agent phase
  verification.json    check results + judge verdict
  REPORT.md            the write-up
```

`AGENTS.md` rather than `CLAUDE.md`, because the runner is deliberately not tied to one
vendor. `pi` reads both.

## Lifecycle

| Status        | Meaning                                               |
| ------------- | ----------------------------------------------------- |
| `draft`       | Created; the question may not be written yet          |
| `designed`    | Pre-registration written, not yet locked              |
| `frozen`      | Predictions and checks are fixed                      |
| `implemented` | Code written under the frozen pre-registration        |
| `run`         | Executed; results present                             |
| `verified`    | Checks passed                                         |
| `refuted`     | Everything produced, but the predictions did not hold |
| `failed`      | The capsule did not produce what it promised          |

The distinction between `refuted` and `failed` is the point. A refuted hypothesis is a
completed, informative result. A failed one is a broken capsule.

## prereg.toml

```toml
hypothesis = "The standard error of the sample mean scales as 1/sqrt(n)."
predictions = [
  "The log-log slope of standard error on n lies strictly between -0.55 and -0.45.",
]
analysis_plan = "Sample Bernoulli(0.3) at n in {25..3200}, 50 seeds each, regress log(se) on log(n)."

# Every key the implementation must write into results/results.json.
# Checks may only reference keys declared here.
[results_contract]
slope = "Log-log regression slope of standard error against n."
n_seeds = "Number of independently seeded batches per sample size."

[[checks]]
id = "slope-near-minus-half"
kind = "expr"
description = "The fitted slope must be close to -0.5."
expr = "abs(results.slope + 0.5) < 0.05"

[[checks]]
id = "convergence-figure"
kind = "artifact"
path = "results/figures/convergence.png"
```

The results contract is what makes the checks runnable: it tells the implementer
exactly which keys to emit, and the checks reference only those keys.

## Freezing

```bash
capsule freeze 0001
```

This hashes `prereg.toml` into `.prereg.lock`. Afterwards:

- `capsule design` refuses to run on the capsule at all.
- Writing a new pre-registration through the library is refused.
- Every subsequent phase re-verifies the hash, **before and after** it runs. The check
  afterwards runs even when the agent reported failure, because a failed run can still
  have modified files.

Tampering produces an actionable error rather than a silent pass:

```
error: pre-registration of capsule 0001 changed after freezing
(expected 2a12ce1b48e9, found 89015b0befec). Restore it from git, or
create a new capsule for the revised prediction.
```

## Revising a frozen pre-registration

Sometimes a registration is simply wrong before any results exist — a check references
a key the contract does not declare, a figure path is misspelled, a threshold is a
typo. Starting a new capsule for that is pointless bookkeeping.

```bash
capsule unfreeze 0001 --reason "the slope check referenced a key the contract omits"
```

This is not an undo. It:

- archives the superseded `prereg.toml` and `.prereg.lock` under `.prereg-history/`,
- records your reason, the superseded hash, and **whether results already existed**,
- moves any existing `verification.json` into the archive, since that verdict was
  reached against predictions that no longer apply,
- returns the capsule to `designed` so it can be revised and re-frozen.

The revision count is then reported in `capsule show`, in the TUI, and in **every
export**, permanently.

!!! warning "Revising after seeing results is a different act"

    Fixing a typo before anything has run is housekeeping. Changing a prediction after
    an outcome is visible is the exact thing pre-registration exists to prevent — so
    the record notes which one happened, and the CLI warns you before letting you do
    the second one.

    If the prediction was substantively wrong rather than mistyped, prefer a new
    capsule. The old one is the record that you expected something else first, and
    that record is worth keeping.

## Provenance

Every capsule records what produced it: runner, provider, model, thinking level,
token count, and cost. capsule-corp does not pin a model, so this is the only honest
account of how a result came about.

```toml
[provenance]
runner = "pi"
runner_version = "0.85.1"
provider = "openai-codex"
model = "gpt-5.6-terra"
executor = "local"
total_tokens = 7789
cost_usd = 0.0193
```

Each agent phase also leaves `runs/<timestamp>/` containing the raw `events.jsonl`
stream and a `meta.json`, so a run can be audited after the fact.
