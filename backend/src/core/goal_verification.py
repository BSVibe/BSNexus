"""Goal artifact verification.

Session 10 longruns showed CEOs emitting `[PROJECT_COMPLETE]` with an
approving-looking checklist even when the workspace had no real code
(only an empty `.bsd` stub and a README). The per-criterion prompt
directive was not enough — GLM treated "task done" as evidence of
"artifact present" and skipped verifying actual files.

This module provides a lightweight, heuristic gate the backend applies
BEFORE flipping ``Project.status`` to ``completed``. It looks at the
Goal description for well-known keyword families (code, design) and
checks the workspace for matching artifacts.

The gate is intentionally conservative: when the Goal has no recognized
keywords, it passes (we'd rather trust CEO than block for unknown
criteria). When a keyword *is* present, a corresponding artifact must
exist, or the marker is rejected.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


# File extensions that count as real, executable source code
_CODE_EXTENSIONS = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".py", ".rb", ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".cc", ".cpp", ".cs", ".php", ".scala",
    ".vue", ".svelte",
}
# Manifest files that also count
_CODE_MANIFESTS = {
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "composer.json", "gemfile", "pom.xml", "build.gradle",
}

# Goal keyword families. Matching is substring-based on the lowercase
# goal description; Korean keywords are matched as-is.
_KEYWORD_CODE = (
    "소스코드", "소스 코드", "source code", "source-code",
    "app source", "앱 소스", "앱 코드", "app code",
    "실행 가능", "실행 가능한 앱", "working app",
    "구현", "implementation",
)
_KEYWORD_DESIGN = (
    "디자인 화면", "디자인화면", "design screen", "design screens",
    "ui/ux", "ui 디자인", "화면 디자인", "screens",
    "와이어프레임", "wireframe",
)


def _workspace_has_real_code(workspace: Path) -> bool:
    """At least one non-trivial code file or manifest under workspace."""
    if not workspace.is_dir():
        return False

    for root, dirs, files in os.walk(workspace):
        # Skip hidden harness dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            path = Path(root) / fname
            ext = path.suffix.lower()
            if ext in _CODE_EXTENSIONS and path.stat().st_size > 0:
                return True
            if fname in _CODE_MANIFESTS and path.stat().st_size > 0:
                return True
    return False


def _workspace_has_real_design(workspace: Path) -> bool:
    """At least one .bsd file that isn't a pure stub.

    An empty stub looks like ``{"spec":{"type":"Screen"},"generated_code":null}``
    — the agent created the row but populated no content. We require
    either ``generated_code`` to be a non-empty string OR ``spec`` to
    contain substantive fields beyond ``type``.
    """
    if not workspace.is_dir():
        return False

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".bsd"):
                continue
            path = Path(root) / fname
            try:
                data = json.loads(path.read_text() or "{}")
            except Exception:
                continue
            code = data.get("generated_code")
            if isinstance(code, str) and code.strip():
                return True
            spec = data.get("spec") or {}
            # A spec that has more than just "type" counts as populated.
            meaningful_keys = [k for k in spec.keys() if k != "type"]
            if meaningful_keys:
                # Require at least one non-empty value among them
                for k in meaningful_keys:
                    v = spec[k]
                    if v:
                        return True
    return False


def _goal_mentions(description: str, keywords: tuple[str, ...]) -> bool:
    lower = description.lower()
    return any(k.lower() in lower for k in keywords)


def verify_goal_artifacts(
    *,
    goal_description: str | None,
    workspace_dir: str | None,
) -> tuple[bool, list[str]]:
    """Heuristically check whether a Goal's stated artifacts exist.

    Returns ``(met, missing)``:
      - ``met``: True if every recognized keyword family has a matching
        artifact, OR the description contains no recognized families
        (then we trust the caller).
      - ``missing``: human-readable labels of families whose evidence
        was not found. Empty when ``met is True``.
    """
    if not goal_description or not workspace_dir:
        return True, []

    workspace = Path(workspace_dir)
    missing: list[str] = []

    if _goal_mentions(goal_description, _KEYWORD_CODE):
        if not _workspace_has_real_code(workspace):
            missing.append("실행 가능한 소스코드 파일 (code / source)")

    if _goal_mentions(goal_description, _KEYWORD_DESIGN):
        if not _workspace_has_real_design(workspace):
            missing.append("디자인 화면 (design screens) — 빈 .bsd stub 은 무효")

    return (len(missing) == 0), missing
