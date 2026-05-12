"""Deliverable → GitHub diff deep link (G8.5).

``build_deliverable_diff_url`` returns the GitHub URL that takes the
reviewer directly to the commit BSNexus landed for a verified
deliverable, or ``None`` when there's no ``commit_sha`` yet.

Commit-level URL (``{repo}/commit/{sha}``) chosen over PR-file anchors
(``#diff-<sha256>``) because:

  - The commit URL is rock-stable.
  - It works even before the PR opens (Request still ``review_ready``)
    and after it merges/closes.
  - PR-file anchors are scoped to a specific revision and brittle when
    GitHub updates their hash algorithm.

Derived field only — nothing is persisted to the database.
"""

from __future__ import annotations

from backend.src.core.github.client import parse_repo_url
from backend.src.models import Deliverable, Project


def build_deliverable_diff_url(*, project: Project, deliverable: Deliverable) -> str | None:
    """Compose ``https://github.com/{owner}/{repo}/commit/{sha}`` when
    both the project repo binding and ``deliverable.commit_sha`` are
    present, else ``None``.
    """
    if not project.github_repo_url:
        return None
    if not deliverable.commit_sha:
        return None
    try:
        owner, repo = parse_repo_url(project.github_repo_url)
    except ValueError:
        return None
    return f"https://github.com/{owner}/{repo}/commit/{deliverable.commit_sha}"
