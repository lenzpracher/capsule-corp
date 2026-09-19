# Exporting a capsule

A capsule is only useful as evidence if someone else can receive it.

```bash
capsule export 0001                        # supplementary-materials zip
capsule export 0001 --format markdown      # an appendix
capsule export 0001 --format html -o fig/  # one self-contained file
```

Every format carries the **pre-registration hash** and the **provenance** — model,
runner version, executor, git commit — because the claim worth making about a capsule
is not _"here are my results"_ but _"here is what I predicted before I looked, and
here is what happened"_.

## Formats

**`bundle`** (default) — a zip of the whole capsule: code, `prereg.toml`,
`.prereg.lock`, `pixi.lock`, results, and figures, plus a generated `EXPORT.md` so a
reader who unzips it knows what they are looking at without installing anything. The
pixi environment and `__pycache__` are excluded; `pixi.lock` is kept, since that is
what actually makes the environment reproducible.

**`markdown`** — an appendix for pasting into a paper or a supplement: provenance
table, hypothesis, predictions, assumptions, analysis plan, the registered checks and
their outcomes, the results JSON, figures, and the judge's verdict.

**`html`** — the same content as one self-contained file, with figures inlined as data
URIs. It survives being emailed and prints cleanly.

## Revisions are disclosed

If the pre-registration was ever unfrozen and revised, every export says so in its own
section, near the top — including how many of those revisions happened _after_ results
already existed.

This is not a footnote by accident. A prediction revised after an outcome was visible
does not carry the evidential weight of one registered beforehand, and a reader cannot
assess the capsule without knowing which they are looking at.

## What to attach to a paper

For most supplements, `bundle` plus the `markdown` appendix is the right pair: the zip
is the artifact, the appendix is the thing a reviewer will actually read.

The `EXPORT.md` inside the bundle is generated from the same source as the appendix,
so they cannot disagree.
