#!/usr/bin/env bash
set -euo pipefail

# Overdrive installer
# Usage: curl -sSL https://raw.githubusercontent.com/Execution-Labs/overdrive/main/scripts/install.sh | bash

REPO="https://github.com/Execution-Labs/overdrive.git"
MIN_PYTHON="3.10"

# --- Helpers ---

info()  { printf "\033[36m==>\033[0m %s\n" "$*"; }
ok()    { printf "\033[32m==>\033[0m %s\n" "$*"; }
err()   { printf "\033[31m==>\033[0m %s\n" "$*" >&2; }
die()   { err "$@"; exit 1; }

check_command() {
    command -v "$1" >/dev/null 2>&1
}

version_gte() {
    printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1 | grep -qx "$2"
}

# --- Checks ---

info "Checking prerequisites..."

# Python
PYTHON=""
for cmd in python3 python; do
    if check_command "$cmd"; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        if version_gte "$ver" "$MIN_PYTHON"; then
            PYTHON="$cmd"
            break
        fi
    fi
done
[ -n "$PYTHON" ] || die "Python $MIN_PYTHON+ is required. Install it from https://python.org"
ok "Python found: $PYTHON ($ver)"

# Git
check_command git || die "git is required"
ok "git found"

# --- Install ---

info "Creating virtual environment..."
VENV_DIR="${OVERDRIVE_VENV_DIR:-$HOME/.overdrive-venv}"
"$PYTHON" -m venv "$VENV_DIR"

info "Installing Overdrive from PyPI..."
"$VENV_DIR/bin/pip" install -q "overdrive[server]"

# --- Shell setup ---

OVERDRIVE_BIN="$VENV_DIR/bin"

# Check if already in PATH
if echo "$PATH" | tr ':' '\n' | grep -qx "$OVERDRIVE_BIN"; then
    ok "overdrive is already in PATH"
else
    SHELL_NAME=$(basename "$SHELL" 2>/dev/null || echo "bash")
    case "$SHELL_NAME" in
        zsh)  RC_FILE="$HOME/.zshrc" ;;
        bash) RC_FILE="$HOME/.bashrc" ;;
        fish) RC_FILE="$HOME/.config/fish/config.fish" ;;
        *)    RC_FILE="$HOME/.profile" ;;
    esac

    printf '\n# Overdrive\nexport PATH="%s:$PATH"\n' "$OVERDRIVE_BIN" >> "$RC_FILE"
    ok "Added $OVERDRIVE_BIN to PATH in $RC_FILE"
    info "Run: source $RC_FILE (or open a new terminal)"
fi

# --- Done ---

echo ""
ok "Overdrive installed successfully!"
echo ""
echo "  Get started:"
echo "    cd /path/to/your/project"
echo "    overdrive server          # UI + API on :8080"
echo ""
echo "  For development (clone + frontend):"
echo "    git clone $REPO && cd overdrive"
echo "    make setup && make dev    # backend :8080 + frontend :3000"
echo ""
