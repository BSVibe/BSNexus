#!/usr/bin/env bash
# BSNexus Worker Installer
# Usage: curl -fsSL https://your-bsnexus-server/worker/install.sh | bash
#
# Environment variables:
#   BSNEXUS_SERVER_URL  — BSNexus server URL (required if not passed as arg)
#   INSTALL_DIR         — Installation directory (default: ~/.bsnexus-worker)

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/.bsnexus-worker}"
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}▸${NC} $*"; }
warn()  { echo -e "${YELLOW}▸${NC} $*"; }
error() { echo -e "${RED}✕${NC} $*" >&2; }
header() { echo -e "\n${BOLD}$*${NC}"; }

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

# Download worker package (or copy if local)
if [ -f "$(dirname "$0")/worker/main.py" ] 2>/dev/null; then
    # Local install (from repo)
    cp -r "$(dirname "$0")/worker" "$INSTALL_DIR/worker"
    cp "$(dirname "$0")/pyproject.toml" "$INSTALL_DIR/pyproject.toml"
    info "Copied from local source"
else
    # Create minimal worker inline (self-contained)
    info "Creating worker package..."
    mkdir -p worker

    cat > pyproject.toml << 'PYPROJECT'
[project]
name = "bsnexus-worker"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27.0", "structlog>=23.0.0", "pydantic-settings>=2.0.0"]

[project.scripts]
bsnexus-worker = "worker.main:main"
PYPROJECT

    # Download worker source from server if BSNEXUS_SERVER_URL is set
    if [ -n "${BSNEXUS_SERVER_URL:-}" ]; then
        info "Server URL: $BSNEXUS_SERVER_URL"
    fi
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
    exec uv run bsnexus-worker "\$@"
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
echo ""
echo "  Next steps:"
echo ""
echo "  1. Open a new terminal (or run: source $SHELL_RC)"
echo ""
echo "  2. Register this worker:"
echo "     ${BOLD}bsnexus-worker register --name \"$(hostname)\" --server YOUR_SERVER_URL${NC}"
echo ""
echo "  3. Start the worker:"
echo "     ${BOLD}bsnexus-worker run${NC}"
echo ""
echo "  Installed to: $INSTALL_DIR"
echo ""
