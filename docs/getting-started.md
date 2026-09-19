# Getting started

## Install

=== "Installer"

    ```bash
    curl -fsSL https://lenzpracher.github.io/capsule-corp/install.sh | sh
    ```

    Installs the `capsule` command into `~/.local/bin` using
    [uv](https://docs.astral.sh/uv/), in its own isolated environment. No sudo, and
    nothing is written outside your home directory.

    As with any installer of this shape, read
    [the script](https://github.com/lenzpracher/capsule-corp/blob/main/install.sh)
    before piping it to a shell.

=== "uv"

    ```bash
    uv tool install git+https://github.com/lenzpracher/capsule-corp
    ```

=== "From a clone"

    ```bash
    git clone https://github.com/lenzpracher/capsule-corp
    cd capsule-corp
    uv tool install --editable .
    ```

    Editable, so the command tracks your working copy. This is the one to use if you
    intend to change capsule-corp itself.

## What else you need

```bash
capsule doctor
```

Two external tools do the real work:

- **[pi](https://github.com/earendil-works/pi)** writes the capsules.
  `npm install -g @earendil-works/pi-coding-agent`, then `pi auth` for at least one
  provider. capsule-corp uses whichever model `pi` is configured with.
- **[pixi](https://pixi.sh)** manages each capsule's environment.

`capsule doctor` reports both, plus which compute backends are usable.

!!! tip "Cross-provider judging"

    If you authenticate two providers with `pi`, you can have the blinded judge run on
    a different model from the implementer. A model is a soft grader of its own output,
    so this is worth doing once you are relying on verdicts.

## Your first capsule

```bash
capsule init ~/research
cd ~/research
```

Create the question. Nothing calls a model yet:

```bash
capsule new "Does the standard error of a Bernoulli(0.3) sample mean shrink as 1/sqrt(n)?"
```

Run the design phase. This writes `QUESTION.md` and `prereg.toml`:

```bash
capsule design 0001
```

**Read `prereg.toml` before going further.** This is the one step where your judgement
matters most — you are deciding whether the registered predictions are the right ones,
and after the next command they cannot change.

Before it is frozen, the design is free to change, so iterate:

```bash
capsule design 0001 -n "Use 50 seeds, and state the i.i.d. assumption explicitly"
```

Re-running `design` on an unfrozen capsule **revises** the existing pre-registration
rather than starting over. Pass `--fresh` if you do want to start over. You can also
just edit `prereg.toml` by hand — it is plain TOML, and nothing about the tool
requires you to go through the model.

```bash
capsule freeze 0001
capsule implement 0001
capsule run 0001
capsule verify 0001
```

```
verifying 0001-bernoulli-standard-error-scaling
  ✓ slope-near-minus-half     abs(results.slope + 0.5) < 0.05 → True
  ✓ enough-seeds              results.n_seeds >= 5 → True
  ✓ convergence-figure        results/figures/convergence.png (65551 bytes)
  3/3 checks passed

  judge       supports (confidence 0.96)

0001 → verified
```

## Reading the code

A capsule you cannot inspect is automated, not reproducible.

```bash
capsule open 0001                  # open the capsule in VS Code
capsule open 0001 --file run.py    # or one file
capsule open 0001 --editor vim
```

`capsule open` uses `$VISUAL`/`$EDITOR` if set, then `editor.command` in settings, then
falls back to whichever of `code`, `cursor`, `zed`, `subl`, `vim`, `nano` it can find.

In the TUI, press `e` for a built-in file browser and editor with syntax highlighting,
or `o` to hand the capsule to your external editor. A frozen `prereg.toml` is readable
there but not editable — you should always be able to read the registration you are
being held to.

## Attaching a capsule to a paper

```bash
capsule export 0001                     # supplementary-materials zip
capsule export 0001 --format markdown   # an appendix
```

See [Exporting](export.md).

## Organising a catalogue

```bash
capsule mkdir optimization
capsule new "Muon vs Adam at 1B" --folder optimization
capsule mv 0001 optimization
capsule tree
capsule search warmup
```

## Interactively

```bash
capsule tui
```

A folder tree, a detail pane showing the pre-registration and verdict, a settings view,
and `r`un / `v`erify / `d`esign / `f`reeze / `i`mplement keybinds. Phases run in worker
threads, so the interface stays responsive while a model is working.

## Settings

```bash
capsule settings show
capsule settings path
capsule settings init          # write a file populated with the current defaults
```

Global settings live in your platform config directory; a catalogue can override them
in `.capsule-corp/settings.toml`. Both are plain TOML.

```toml
[pixi]
default_packages = ["python=3.12", "numpy", "polars", "matplotlib"]

[agent]
# Leave provider and model unset to inherit whatever pi is configured with.
thinking = "high"

[executors]
default = "local"
```
