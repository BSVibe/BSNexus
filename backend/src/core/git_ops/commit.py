"""Deliverable → repo branch commit (G8.2).

``commit_deliverable`` reads the artifact files from the project's
workspace, ensures the per-Request branch exists, then PUTs each file
to GitHub via the Contents API. The last commit's SHA is persisted on
``Deliverable.commit_sha`` so G8.3 (PR creation) can stitch the commit
graph back to the founder-visible Brief.

Failure model: callers (the VerifierWorker) wrap the call in
try/except. A commit failure does NOT roll back ``proof_state=verified``
— a verified deliverable that didn't land on the branch is a soft
warning, not a verification regression. The next manual re-verify
retries cleanly because ``commit_sha`` stays NULL.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.git_ops.branch import (
    BranchOpError,
    GithubClientFactory,
    ensure_request_branch,
)
from backend.src.core.github import GithubAuthError, GithubError, parse_repo_url
from backend.src.models import Deliverable, Project, Request, WorkStep

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CommitResult:
    deliverable_id: uuid.UUID
    branch_name: str
    commit_sha: str
    paths_committed: list[str]
    paths_skipped: list[str]


class CommitOpError(Exception):
    """Raised when ``commit_deliverable`` cannot land any artifact.

    ``reason`` mirrors :class:`BranchOpError.reason` plus commit-specific
    values:

      - all branch-op reasons (forwarded from ``ensure_request_branch``)
      - ``no_artifacts``        — deliverable has no artifact_refs entries.
      - ``no_workspace``        — project has no workspace_dir or path missing.
      - ``no_request``          — deliverable.request_id is null.
      - ``github_unavailable``  — Contents API failed for every artifact.
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


def _artifact_paths(deliverable: Deliverable) -> list[str]:
    refs = deliverable.artifact_refs or []
    paths: list[str] = []
    for ref in refs:
        if isinstance(ref, dict):
            path = ref.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
        elif isinstance(ref, str) and ref:
            paths.append(ref)
    return paths


async def commit_deliverable(
    *,
    deliverable: Deliverable,
    session: AsyncSession,
    client_factory: GithubClientFactory | None = None,
) -> CommitResult:
    """Land every artifact file on the request's branch.

    Idempotent against partial failures: a file that already exists at
    the same content (same blob SHA) is skipped without rewriting. A
    file that diverged is updated. Files that fail to read from the
    workspace go into ``paths_skipped``; the call only raises if NO
    file was successfully written.
    """
    if deliverable.request_id is None:
        raise CommitOpError("no_request", "deliverable is not bound to a request")
    paths = _artifact_paths(deliverable)
    if not paths:
        raise CommitOpError("no_artifacts", "deliverable has no artifact_refs")

    project = await session.get(Project, deliverable.project_id)
    if project is None:
        raise CommitOpError("no_workspace", "project not found")
    workspace_root = Path(project.workspace_dir) if project.workspace_dir else None
    if workspace_root is None or not workspace_root.exists():
        raise CommitOpError("no_workspace", "project has no on-disk workspace")

    request_row = await session.get(Request, deliverable.request_id)
    if request_row is None:
        raise CommitOpError("no_request", "deliverable.request_id is dangling")

    # Ensure branch — forwards BranchOpError.reason via CommitOpError.
    try:
        branch_info = await ensure_request_branch(request=request_row, session=session, client_factory=client_factory)
    except BranchOpError as exc:
        raise CommitOpError(exc.reason, str(exc)) from exc

    owner, repo = parse_repo_url(branch_info.repo_url)

    # Decrypt the same token ensure_request_branch already used. To
    # avoid an extra DB roundtrip we lift the encrypted value off the
    # already-loaded project row.
    from backend.src.config import settings as app_settings
    from backend.src.core.encryption import EncryptionManager

    token = EncryptionManager(app_settings.encryption_key).decrypt_value(project.github_token_encrypted or "")

    work_step = await session.get(WorkStep, deliverable.work_step_id) if deliverable.work_step_id else None
    message = _build_commit_message(deliverable=deliverable, work_step=work_step)

    written: list[str] = []
    skipped: list[str] = []
    last_commit_sha: str | None = None

    from backend.src.core.github import GithubClient

    if client_factory is None:
        client = GithubClient(token=token)
    else:
        client = client_factory(token)  # type: ignore[assignment]
        if hasattr(client, "__await__"):
            client = await client  # type: ignore[assignment]

    try:
        for rel_path in paths:
            abs_path = (workspace_root / rel_path).resolve()
            try:
                abs_path.relative_to(workspace_root.resolve())
            except ValueError:
                logger.warning(
                    "commit_skip_outside_workspace",
                    deliverable_id=str(deliverable.id),
                    path=rel_path,
                )
                skipped.append(rel_path)
                continue
            if not abs_path.exists() or not abs_path.is_file():
                logger.warning(
                    "commit_skip_missing_file",
                    deliverable_id=str(deliverable.id),
                    path=rel_path,
                )
                skipped.append(rel_path)
                continue

            content = abs_path.read_bytes()
            try:
                existing_sha = await client.get_file_sha(owner, repo, rel_path, branch_info.name)
                resp = await client.put_file_content(
                    owner,
                    repo,
                    rel_path,
                    content=content,
                    message=message,
                    branch=branch_info.name,
                    sha=existing_sha,
                )
            except GithubAuthError as exc:
                raise CommitOpError("github_auth", str(exc)) from exc
            except GithubError as exc:
                # Per-file failure: log and skip; the loop continues
                # so a one-off API blip doesn't lose everything.
                logger.warning(
                    "commit_per_file_failed",
                    deliverable_id=str(deliverable.id),
                    path=rel_path,
                    status_code=exc.status_code,
                    message=str(exc),
                )
                skipped.append(rel_path)
                continue
            commit_block = resp.get("commit") if isinstance(resp, dict) else None
            if isinstance(commit_block, dict):
                sha = commit_block.get("sha")
                if isinstance(sha, str):
                    last_commit_sha = sha
            written.append(rel_path)
    finally:
        await client.aclose()

    if not written:
        raise CommitOpError(
            "github_unavailable",
            f"no artifact landed on {branch_info.name}; skipped={skipped}",
        )

    # Persist commit_sha on the deliverable — caller commits the txn.
    deliverable.commit_sha = last_commit_sha
    logger.info(
        "deliverable_committed",
        deliverable_id=str(deliverable.id),
        branch=branch_info.name,
        commit_sha=last_commit_sha,
        paths_committed=written,
        paths_skipped=skipped,
    )
    assert last_commit_sha is not None  # written != [] implies at least one PUT returned a sha
    return CommitResult(
        deliverable_id=deliverable.id,
        branch_name=branch_info.name,
        commit_sha=last_commit_sha,
        paths_committed=written,
        paths_skipped=skipped,
    )


def _build_commit_message(*, deliverable: Deliverable, work_step: WorkStep | None) -> str:
    head = deliverable.title or "Deliverable"
    if work_step is not None and work_step.name:
        head = f"{work_step.name}: {head}"
    body = f"\n\nDeliverable: {deliverable.id}"
    if deliverable.summary:
        body = f"\n\n{deliverable.summary}{body}"
    return head + body
