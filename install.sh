#!/bin/sh
# capsule-corp installer.
#
#   curl -fsSL https://lenzpracher.github.io/capsule-corp/install.sh | sh
#
# Installs the `capsule` command into ~/.local/bin using uv, which keeps it in its
# own isolated environment rather than in your system Python. Nothing here needs
# sudo, and nothing is written outside your home directory.
#
# Read before piping to a shell. That advice applies to every installer of this
# shape, including this one.

set -eu

REPO="${CAPSULE_CORP_REPO:-lenzpracher/capsule-corp}"
REF="${CAPSULE_CORP_REF:-main}"
SOURCE="git+https://github.com/${REPO}@${REF}"

BOLD=""
DIM=""
RED=""
GREEN=""
YELLOW=""
RESET=""
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD="$(printf '\033[1m')"
    DIM="$(printf '\033[2m')"
    RED="$(printf '\033[31m')"
    GREEN="$(printf '\033[32m')"
    YELLOW="$(printf '\033[33m')"
    RESET="$(printf '\033[0m')"
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s  %s%s\n' "${GREEN}ok${RESET}" "$*" "${RESET}"; }
warn() { printf '%s %s\n' "${YELLOW}note${RESET}" "$*"; }
die()  { printf '%serror%s %s\n' "${RED}" "${RESET}" "$*" >&2; exit 1; }

has() { command -v "$1" >/dev/null 2>&1; }

say ""
say "${BOLD}capsule-corp${RESET} ${DIM}— packaging reproducible research questions${RESET}"
say ""

case "$(uname -s)" in
    Linux | Darwin) ;;
    *) die "unsupported platform $(uname -s). On Windows, use WSL." ;;
esac

# --- uv -------------------------------------------------------------------

if has uv; then
    ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"
else
    say "installing uv ${DIM}(used to keep capsule-corp isolated)${RESET}"
    if has curl; then
        curl -fsSL https://astral.sh/uv/install.sh | sh
    elif has wget; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        die "need curl or wget to install uv. Install uv yourself: https://docs.astral.sh/uv/"
    fi
    # The uv installer puts it here but does not touch the current shell.
    for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        [ -x "$candidate/uv" ] && PATH="$candidate:$PATH"
    done
    export PATH
    has uv || die "uv was installed but is not on PATH. Open a new shell and re-run this script."
    ok "uv installed"
fi

# --- capsule-corp ---------------------------------------------------------

say "installing capsule ${DIM}from ${REPO}@${REF}${RESET}"
uv tool install --force "$SOURCE" >/dev/null 2>&1 || uv tool install --force "$SOURCE"

BIN_DIR="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
[ -x "$BIN_DIR/capsule" ] || die "installation finished but $BIN_DIR/capsule is missing"
ok "capsule installed to $BIN_DIR/capsule"

# --- PATH -----------------------------------------------------------------

case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *)
        warn "${BIN_DIR} is not on your PATH. Add this to your shell profile:"
        say ""
        say "    export PATH=\"${BIN_DIR}:\$PATH\""
        say ""
        ;;
esac

# --- what capsule-corp needs to actually do anything ----------------------

say ""
if has pi; then
    ok "pi $(pi --version 2>/dev/null)"
else
    warn "pi is not installed. capsule-corp uses it to write capsules:"
    say "    npm install -g @earendil-works/pi-coding-agent"
    say "    pi auth"
fi

if has pixi; then
    ok "pixi"
else
    warn "pixi is not installed. Capsules use it for their environments:"
    say "    curl -fsSL https://pixi.sh/install.sh | sh"
fi

say ""
say "${BOLD}next${RESET}"
say "  capsule doctor                 ${DIM}check everything is wired up${RESET}"
say "  capsule init ~/research        ${DIM}create a catalogue${RESET}"
say "  capsule new \"your question\"    ${DIM}then: design, freeze, implement, run, verify${RESET}"
say "  capsule tui                    ${DIM}browse it interactively${RESET}"
say ""
say "${DIM}docs: https://lenzpracher.github.io/capsule-corp${RESET}"
say ""
