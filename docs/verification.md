# Verification

Two independent layers, in this order.

## Layer 1: deterministic checks

The pre-registered checks are evaluated mechanically, with **no language model
involved**. That is what makes them a gate rather than an opinion.

```bash
capsule verify 0001 --no-judge
```

`expr` checks are Python expressions over `results.json`:

```toml
expr = "abs(results.slope + 0.5) < 0.05"
expr = "10.2 < results.endpoint_se_ratio < 12.4"   # chained comparisons work
expr = "len(results.seeds) >= 5"
```

These run in a restricted evaluator that walks the AST itself rather than calling
`eval`. Dispatch is table-driven and closed: a node type with no handler is refused
rather than falling through to something permissive. Available helpers are `abs`,
`all`, `any`, `len`, `max`, `min`, `round`, and `sum`; imports, attribute access on
anything but `results`, comprehensions, lambdas, and arbitrary calls are all rejected.

`artifact` checks assert a file exists and is non-empty:

```toml
kind = "artifact"
path = "results/figures/convergence.png"
```

## Layer 2: the blinded judge

A language model reads the question, the pre-registration, the code, and the outputs,
and says whether the evidence supports the hypothesis.

It is blinded **structurally**. The judge runs in a copied workspace built from an
allowlist, so the write-up, the previous verdict, the manifest, and the run history are
absent by construction — not by an instruction a model could ignore.

| Copied                        | Withheld                     |
| ----------------------------- | ---------------------------- |
| `QUESTION.md`                 | `REPORT.md`                  |
| `prereg.toml`, `.prereg.lock` | `verification.json`          |
| `run.py`, `src/`, `pixi.toml` | `capsule.toml`               |
| `results/`                    | `runs/`, `.git`, `AGENTS.md` |

`.prereg.lock` is included on purpose: it carries only a hash, a timestamp and the
check ids, and without it the judge cannot confirm that the pre-registration it is
reading is the one that was frozen. That was a real complaint from a live judge run.

The judge is also invoked with `--no-builtin-tools` and a read-only tool list, because
blinding is worthless if it can edit what it is judging.

It returns a structured verdict:

```json
{
  "supports_hypothesis": false,
  "confidence": 0.7,
  "reasoning": "The effect direction is opposite to H1; see fig-2.",
  "concerns": ["Only 3 seeds; the error bars overlap."]
}
```

`supports_hypothesis` is three-valued. A judge that genuinely cannot tell from what is
in front of it should say `null` rather than guess.

## How the outcome is decided

| Situation                      | Outcome                                                      |
| ------------------------------ | ------------------------------------------------------------ |
| A check could not be evaluated | `failed`                                                     |
| An `artifact` check failed     | `failed` — the capsule did not produce what it promised      |
| An `expr` check failed         | `refuted` — a completed result whose prediction did not hold |
| Everything passed              | `verified`                                                   |
| Judge disagrees, `--strict`    | `refuted`                                                    |

By default the judge's verdict is **recorded but not blocking**. Use `--strict` to let
it change the outcome.

```bash
capsule verify 0001 --strict
```

!!! note "Why an expr failure is not a failure"

    A failing prediction could mean a bug, or it could mean the hypothesis was wrong.
    Nothing in the artifact can distinguish those, and treating it as breakage would
    quietly bias the catalogue toward confirmed results. `refuted` is the honest
    default; the judge's concerns are the place to look for a suspected bug.
