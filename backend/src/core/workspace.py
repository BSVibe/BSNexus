"""Project workspace — where extracted code files land on disk.

Each project gets its own directory so the founder can actually hand
the tree to a developer (or the same LLM in a follow-up run) as real
files, not markdown blobs.

Kept intentionally simple: a local filesystem path under
``settings.workspace_root`` (defaults to ``./data/workspaces``) keyed
by ``project_id``. Swap in a git-backed or S3-backed store later by
matching this API.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import structlog

from backend.src.config import settings

logger = structlog.get_logger(__name__)


def _root() -> Path:
    configured = getattr(settings, "workspace_root", None)
    base = Path(configured) if configured else Path.cwd() / "data" / "workspaces"
    base.mkdir(parents=True, exist_ok=True)
    return base


def project_workspace_path(project_id: uuid.UUID) -> Path:
    """Return (creating if missing) the workspace directory for a project."""
    path = _root() / str(project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_file(
    project_id: uuid.UUID,
    relative_path: str,
    content: str,
) -> Path:
    """Write ``content`` to the project workspace. Refuses paths that
    escape the workspace root so a malicious relative path in an LLM
    output can't overwrite arbitrary host files."""
    root = project_workspace_path(project_id)
    # Normalize & reject any .. segments that would leave the root.
    normalized = os.path.normpath(relative_path)
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(f"rejecting absolute / escaping path: {relative_path!r}")
    target = (root / normalized).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"path escapes workspace root: {relative_path!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def list_files(project_id: uuid.UUID) -> list[dict[str, object]]:
    """Return a flat list of files under the workspace with size bytes."""
    root = project_workspace_path(project_id)
    out: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            out.append({"path": rel, "size": size})
    return out


def read_file(project_id: uuid.UUID, relative_path: str) -> str | None:
    """Read a single file as text. Returns None if it's missing or
    outside the workspace."""
    root = project_workspace_path(project_id)
    normalized = os.path.normpath(relative_path)
    if normalized.startswith("..") or os.path.isabs(normalized):
        return None
    target = (root / normalized).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        return None
    if not target.is_file():
        return None
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
