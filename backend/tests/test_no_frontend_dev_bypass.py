"""S1-3 — frontend must NOT carry a client-side auth-bypass token.

The "No dev-login bypass" rule (BSVibe Lockin §BSVibe Production) bans
``VITE_DEV_BYPASS_TOKEN`` and any equivalent synthetic-user shortcut in
production frontend builds. The current ``useAuth.ts`` already routes
all auth through BSVibe-Auth (``auth.bsvibe.dev``), so this test is a
regression guard against accidentally reintroducing such a path.

It scans every ``frontend/src/`` and ``frontend/index.html`` file for
the forbidden patterns. The patterns are intentionally narrow — they
match concrete identifiers an auth-bypass implementation would use,
not generic words like "bypass" that could appear in unrelated
comments.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo root = parents of this file: backend/tests/<file> → ../../
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
FRONTEND_INDEX = REPO_ROOT / "frontend" / "index.html"

_FORBIDDEN_PATTERNS = [
    # No client-side bypass token env var.
    r"VITE_DEV_BYPASS_TOKEN",
    r"VITE_DEV_BYPASS",
    # No synthetic dev-user object.
    r"DEV_BYPASS_USER",
    r"createDevBypassUser",
]


def _candidate_files() -> list[Path]:
    files: list[Path] = []
    if FRONTEND_SRC.exists():
        for ext in ("ts", "tsx", "js", "jsx"):
            files.extend(FRONTEND_SRC.rglob(f"*.{ext}"))
    if FRONTEND_INDEX.exists():
        files.append(FRONTEND_INDEX)
    return files


@pytest.mark.parametrize("pattern", _FORBIDDEN_PATTERNS)
def test_no_dev_bypass_pattern_in_frontend_source(pattern: str):
    files = _candidate_files()
    if not files:
        pytest.skip("frontend source not present in this checkout")

    rx = re.compile(pattern)
    offenders: list[str] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if rx.search(content):
            rel = path.relative_to(REPO_ROOT)
            offenders.append(str(rel))

    assert not offenders, (
        f"Forbidden auth-bypass pattern {pattern!r} found in frontend source. "
        "Per BSVibe Lockin 'No dev-login bypass' rule, the frontend must "
        "authenticate exclusively via BSVibe-Auth (auth.bsvibe.dev). "
        f"Offending files: {offenders}"
    )
