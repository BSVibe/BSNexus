"""Request → GitHub PR (G8.3).

``open_request_pr`` is the single entry point: given a Request that
has shipped Deliverables on its ``bsnexus/req-<id>`` branch, it opens
(or returns the existing) GitHub PR against the project's base branch
and stamps ``Request.pr_number`` + ``Request.pr_url``.

Idempotent. If a PR for the same ``head`` already exists on the repo
(any state), we return that PR rather than opening a duplicate. This
matters because the chokepoint hook in ``transition_request`` may fire
again if the founder re-ships a previously shipped Request (e.g., a
manual retry after a soft failure).

Failure model — soft. The caller (``transition_request``) wraps the
call in try/except. A PR creation failure does NOT revert
``shipped``: the proof-of-completion contract is the verified
Deliverables, not the GitHub PR. The next manual retry runs cleanly
because ``pr_number`` stays NULL.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.core.git_ops.branch import (
    BranchOpError,
    GithubClientFactory,
    ensure_request_branch,
)
from backend.src.core.github import (
    GithubAuthError,
    GithubClient,
    GithubError,
    parse_repo_url,
)
from backend.src.models import Project, Request

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PullRequestInfo:
    request_id: uuid.UUID
    repo_url: str
    branch_name: str
    base_branch: str
    pr_number: int
    pr_url: str
    created: bool


class PullRequestOpError(Exception):
    """Raised when ``open_request_pr`` cannot complete.

    ``reason`` codes (stable for API mapping):

      - All branch-op reasons (``repo_not_bound`` / ``missing_token``
        / ``invalid_repo_url`` / ``base_branch_not_found`` /
        ``github_auth`` / ``github_unavailable``).
      - ``empty_branch``         — request branch exists but the same
        SHA as the base, so there is nothing to PR (GitHub would 422
        with "No commits between branches").
      - ``no_request_branch``    — ensure_request_branch reported
        ``created=True``; opening a PR off an empty new branch makes
        no sense.
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


def _default_pr_title(request: Request) -> str:
    intent = (request.intent or "").strip().splitlines()[0] if request.intent else ""
    if not intent:
        intent = f"Request {request.id}"
    if len(intent) > 70:
        intent = intent[:67].rstrip() + "..."
    return intent


def _placeholder_pr_body(request: Request) -> str:
    """G8.3 ships a minimal PR body. G8.4 will replace this with the
    founder-summary / proof / decisions / risks composer; the placeholder
    is intentional so reviewers can recognize the stand-in.
    """
    lines = [
        f"Request: `{request.id}`",
        "",
        f"Intent:\n\n> {(request.intent or '').strip() or '(no intent recorded)'}",
        "",
        "_Body will be filled in by the BSNexus PR composer (G8.4)._",
    ]
    return "\n".join(lines)


async def open_request_pr(
    *,
    request: Request,
    session: AsyncSession,
    client_factory: GithubClientFactory | None = None,
    title: str | None = None,
    body: str | None = None,
) -> PullRequestInfo:
    """Open or return the GitHub PR for ``request``.

    ``session`` is used to read the bound Project + decrypt the PAT
    and to persist ``pr_number`` / ``pr_url`` on the Request row. The
    function flushes but does NOT commit — caller controls the
    transaction (matches the rest of ``core/git_ops``).
    """
    project = await session.get(Project, request.project_id)
    if project is None or not project.github_repo_url:
        raise PullRequestOpError("repo_not_bound", "project has no repo binding")
    if not project.github_token_encrypted:
        raise PullRequestOpError("missing_token", "repo bound but PAT not stored")

    try:
        owner, repo = parse_repo_url(project.github_repo_url)
    except ValueError as exc:
        raise PullRequestOpError("invalid_repo_url", str(exc)) from exc

    try:
        branch_info = await ensure_request_branch(request=request, session=session, client_factory=client_factory)
    except BranchOpError as exc:
        raise PullRequestOpError(exc.reason, str(exc)) from exc

    if branch_info.created:
        # Branch was just created off base — no commits, no PR to open.
        raise PullRequestOpError(
            "no_request_branch",
            f"request branch {branch_info.name!r} was just created; no commits to PR",
        )

    token = EncryptionManager(app_settings.encryption_key).decrypt_value(project.github_token_encrypted)
    client = _resolve_client(client_factory, token)
    if hasattr(client, "__await__"):
        client = await client  # type: ignore[assignment]

    try:
        try:
            existing = await client.list_pulls(owner, repo, head=branch_info.name, state="all")
        except GithubAuthError as exc:
            raise PullRequestOpError("github_auth", str(exc)) from exc
        except GithubError as exc:
            raise PullRequestOpError("github_unavailable", str(exc)) from exc

        if existing:
            pr = existing[0]
            number = int(pr.get("number") or 0)
            url = str(pr.get("html_url") or "")
            request.pr_number = number
            request.pr_url = url
            await session.flush()
            logger.info(
                "pr_already_exists",
                request_id=str(request.id),
                pr_number=number,
                pr_url=url,
            )
            return PullRequestInfo(
                request_id=request.id,
                repo_url=project.github_repo_url,
                branch_name=branch_info.name,
                base_branch=branch_info.base_branch,
                pr_number=number,
                pr_url=url,
                created=False,
            )

        try:
            pr = await client.create_pull(
                owner,
                repo,
                title=title or _default_pr_title(request),
                head=branch_info.name,
                base=branch_info.base_branch,
                body=body if body is not None else _placeholder_pr_body(request),
            )
        except GithubAuthError as exc:
            raise PullRequestOpError("github_auth", str(exc)) from exc
        except GithubError as exc:
            # GitHub returns 422 for "No commits between" — surface as
            # empty_branch so the founder sees a precise reason rather
            # than a generic 502.
            message = str(exc)
            if exc.status_code == 422 and "no commits" in message.lower():
                raise PullRequestOpError("empty_branch", message) from exc
            raise PullRequestOpError("github_unavailable", message) from exc

        number = int(pr.get("number") or 0)
        url = str(pr.get("html_url") or "")
        request.pr_number = number
        request.pr_url = url
        await session.flush()
        logger.info(
            "pr_created",
            request_id=str(request.id),
            pr_number=number,
            pr_url=url,
            head=branch_info.name,
            base=branch_info.base_branch,
        )
        return PullRequestInfo(
            request_id=request.id,
            repo_url=project.github_repo_url,
            branch_name=branch_info.name,
            base_branch=branch_info.base_branch,
            pr_number=number,
            pr_url=url,
            created=True,
        )
    finally:
        await client.aclose()


def _resolve_client(factory: GithubClientFactory | None, token: str) -> GithubClient | Awaitable[GithubClient]:
    if factory is None:
        return GithubClient(token=token)
    return factory(token)
