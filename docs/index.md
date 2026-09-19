---
hide:
  - navigation
  - toc
---

<div class="cc-hero">
  <img class="cc-mark" src="assets/logo-mark.svg" alt="">
  <h1>capsule</h1>
</div>

<p class="cc-tagline" markdown>
A terminal catalogue where each **capsule** is one empirically testable research
question — packaged so the result is reproducible and cannot be quietly retrofitted to
the conclusion.
</p>

<div class="cc-install" markdown>
```bash
curl -fsSL https://lenzpracher.github.io/capsule-corp/install.sh | sh
```
</div>

## The problem

The failure mode of LLM-generated research is not bad code. It is code that gets
rewritten, quietly, until it agrees with the conclusion. Nothing in the final artifact
records that this happened — the commit history shows a tidy result, the plot looks
right, and the claim is unfalsifiable after the fact.

## What capsule-corp does about it

It splits a capsule into phases with a lock in between.

<div class="cc-grid" markdown>
<div markdown>
### 1. Design
An agent writes the question, the hypothesis, and machine-checkable assertions that
would falsify it — **before any implementation exists**.
</div>
<div markdown>
### 2. Freeze
`prereg.toml` is hashed into `.prereg.lock`. From here the predictions and the checks
cannot change.
</div>
<div markdown>
### 3. Implement
A second agent writes the code. The hash is verified before *and* after. If the
pre-registration moved, the run fails.
</div>
<div markdown>
### 4. Verify
Deterministic checks decide whether the predictions held. Then a **blinded** judge
reads the code and outputs — but never the write-up.
</div>
</div>

A capsule that cannot fail cannot be frozen: the design phase is rejected if it
registers no falsifiable check.

!!! quote "A refuted hypothesis is a successful capsule"

    A capsule that produced everything it promised but whose predictions did not hold
    is marked `refuted`, not `failed`. Those are different outcomes and they are stored
    differently — which means the catalogue accumulates the negative results that
    normally go unrecorded anywhere.

## A capsule on disk

Everything is plain text, git-friendly, and editable by hand. The tool is a convenience
over the files, never a gatekeeper to them.

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

## Not tied to one vendor

The coding agent is [`pi`](https://github.com/earendil-works/pi) — open source and
provider-agnostic. Configure it for Claude, GPT, Gemini, or a local model.
capsule-corp never pins a model; it inherits whatever `pi` is set to and records what
was actually used in each capsule's manifest.

The catalogue itself speaks [MCP](mcp.md), so any client can read it — including
`capsule://<id>` resources that let an agent check what you have already tested before
spending compute on a settled question.

## Runs where you do

Local by default. Unchanged on Slurm, any SSH host with Docker, or Modal.

```bash
capsule run 0001 --on slurm
```

[Get started :material-arrow-right:](getting-started.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/lenzpracher/capsule-corp){ .md-button }

---

!!! info "Disclaimer"

    capsule-corp is an independent, unaffiliated open-source project. It is not
    associated with, endorsed by, or sponsored by Bird Studio, Shueisha, Toei Animation,
    or Capsule Corporation Tokyo. "Dragon Ball" and "Capsule Corporation" are trademarks
    of their respective owners; the name and mark here are an affectionate nod, and no
    rights-holder artwork is used.
