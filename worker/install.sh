#!/usr/bin/env bash
# BSNexus Worker Installer
# Usage: curl -fsSL .../install.sh | bash
#
# Environment variables (optional):
#   BSNEXUS_SERVER_URL, INSTALL_DIR

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/.bsnexus-worker}"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { printf '%b\n' "${GREEN}▸${NC} $*"; }
warn()  { printf '%b\n' "${YELLOW}▸${NC} $*"; }
error() { printf '%b\n' "${RED}✕${NC} $*" >&2; }
header() { printf '\n%b\n' "${BOLD}$*${NC}"; }

# ─── Check prerequisites ─────────────────────────────────────────

header "BSNexus Worker Installer"
echo ""

check_cmd() {
    if ! command -v "$1" &>/dev/null; then
        error "$1 not found."
        echo "  $2"
        return 1
    fi
    info "$1 found: $(command -v "$1")"
}

MISSING=0
check_cmd python3 "Install Python 3.11+: https://python.org" || MISSING=1
check_cmd claude  "Install Claude Code: npm install -g @anthropic-ai/claude-code" || MISSING=1

# uv is preferred but pip works too
HAS_UV=0
if command -v uv &>/dev/null; then
    info "uv found: $(command -v uv)"
    HAS_UV=1
else
    warn "uv not found (optional, will use pip instead)"
    warn "  Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if [ "$MISSING" -eq 1 ]; then
    echo ""
    error "Missing prerequisites. Install them and re-run."
    exit 1
fi

# Check Python version
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    error "Python 3.11+ required, found $PY_VERSION"
    exit 1
fi
info "Python $PY_VERSION"

# ─── Install ──────────────────────────────────────────────────────

header "Installing to $INSTALL_DIR"

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Detect server URL from the URL this script was fetched from
# e.g. http://bsserver:3000/api/v1/workers/install.sh → http://bsserver:3000
SERVER_URL="${BSNEXUS_SERVER_URL:-}"

# Download worker source from server
if [ -n "$SERVER_URL" ]; then
    info "Downloading worker source from $SERVER_URL..."
    if curl -fsSL "$SERVER_URL/api/v1/workers/source.tar.gz" | tar xz; then
        info "Downloaded worker source"
    else
        error "Failed to download worker source. Check your install token."
        exit 1
    fi
elif [ -f "$(dirname "$0")/worker/main.py" ] 2>/dev/null; then
    # Local install (from repo checkout)
    cp -r "$(dirname "$0")/worker" "$INSTALL_DIR/worker"
    cp "$(dirname "$0")/pyproject.toml" "$INSTALL_DIR/pyproject.toml"
    info "Copied from local source"
else
    error "Cannot determine server URL. Set BSNEXUS_SERVER_URL and re-run:"
    error "  BSNEXUS_SERVER_URL=http://your-server:3000 curl -fsSL .../install.sh | bash"
    exit 1
fi

# Install dependencies
if [ "$HAS_UV" -eq 1 ]; then
    info "Installing with uv..."
    uv sync --quiet 2>/dev/null || uv pip install -e . --quiet
else
    info "Installing with pip..."
    python3 -m pip install -e . --quiet
fi

# ─── Create shell wrapper ────────────────────────────────────────

WRAPPER="$INSTALL_DIR/bsnexus-worker"
cat > "$WRAPPER" << WRAPPER_SCRIPT
#!/usr/bin/env bash
cd "$INSTALL_DIR"
if command -v uv &>/dev/null; then
    exec uv run python -m worker.main "\$@"
else
    exec python3 -m worker.main "\$@"
fi
WRAPPER_SCRIPT
chmod +x "$WRAPPER"

# ─── Add to PATH ─────────────────────────────────────────────────

SHELL_RC=""
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

PATH_LINE="export PATH=\"$INSTALL_DIR:\$PATH\""
if [ -n "$SHELL_RC" ] && ! grep -qF "bsnexus-worker" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# BSNexus Worker" >> "$SHELL_RC"
    echo "$PATH_LINE" >> "$SHELL_RC"
    info "Added to PATH in $SHELL_RC"
else
    info "PATH already configured or no shell RC found"
fi

# ─── Done ─────────────────────────────────────────────────────────

header "Installation complete!"
printf '\n'
printf '  Next steps:\n'
printf '\n'
printf '  1. Open a new terminal (or run: source %s)\n' "$SHELL_RC"
printf '\n'
printf '  2. Register this worker:\n'
printf '     %bbsnexus-worker register --name "%s" --server YOUR_SERVER_URL%b\n' "$BOLD" "$(hostname)" "$NC"
printf '\n'
printf '  3. Start the worker:\n'
printf '     %bbsnexus-worker run%b\n' "$BOLD" "$NC"
printf '\n'
printf '  Installed to: %s\n\n' "$INSTALL_DIR"
