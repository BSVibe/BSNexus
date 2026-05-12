"""Request-time branch operations (G8.1).

``ensure_request_branch`` is the single entry point: given a Request,
it loads the Project's repo binding, decrypts the PAT, and either
creates ``bsnexus/req-<request_id>`` off the configured base branch or
returns the existing ref. Idempotent — a second call after the branch
exists returns the same SHA without a write.

Why a Request-scoped branch (not e.g. WorkStep): a single founder
Direction → Request can fan out into many WorkSteps and Deliverables.
They all land on the same branch and ship as one PR in G8.3. That
keeps the founder's review surface coarse — one PR per ask.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.config import settings as app_settings
from backend.src.core.encryption import EncryptionManager
from backend.src.core.github import (
    GithubClient,
    GithubError,
    GithubNotFound,
    parse_repo_url,
)
from backend.src.models import Project, Request

logger = structlog.get_logger(__name__)


class BranchOpError(Exception):
    """Raised when ``ensure_request_branch`` cannot complete.

    ``reason`` is a stable short string the API layer maps to a 4xx,
    not a free-form message. Known values:

      - ``repo_not_bound`` — Project has no ``github_repo_url``.
      - ``missing_token`` — repo bound but no PAT stored.
      - ``invalid_repo_url`` — URL did not parse as github.com/owner/repo.
      - ``base_branch_not_found`` — configured ``github_branch`` does
        not exist on the remote.
      - ``github_auth`` — 401/403 from GitHub.
      - ``github_unavailable`` — other GitHub error.
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class BranchInfo:
    name: str
    sha: str
    base_branch: str
    repo_url: str
    created: bool


def build_request_branch_name(request_id: uuid.UUID) -> str:
    return f"bsnexus/req-{request_id}"


# Factory injected for tests; defaults to a real ``GithubClient`` per call.
GithubClientFactory = Callable[[str], Awaitable[GithubClient] | GithubClient]


async def ensure_request_branch(
    *,
    request: Request,
    session: AsyncSession,
    client_factory: GithubClientFactory | None = None,
) -> BranchInfo:
    """Create or return the branch for ``request``.

    ``session`` is used to load the project + binding. The function
    does not commit — caller controls the transaction.
    """
    project = await session.get(Project, request.project_id)
    if project is None or not project.github_repo_url:
        raise BranchOpError("repo_not_bound", "project has no repo binding")
    if not project.github_token_encrypted:
        raise BranchOpError("missing_token", "repo bound but PAT not stored")

    try:
        owner, repo = parse_repo_url(project.github_repo_url)
    except ValueError as exc:
        raise BranchOpError("invalid_repo_url", str(exc)) from exc

    token = EncryptionManager(app_settings.encryption_key).decrypt_value(project.github_token_encrypted)
    base_branch = (project.github_branch or "main").strip() or "main"
    branch_name = build_request_branch_name(request.id)

    client = await _resolve_client(client_factory, token)
    try:
        existing = await client.get_ref(owner, repo, branch_name)
        if existing is not None:
            sha = existing["object"]["sha"]
            logger.info(
                "branch_already_exists",
                request_id=str(request.id),
                branch=branch_name,
                sha=sha,
            )
            return BranchInfo(
                name=branch_name,
                sha=sha,
                base_branch=base_branch,
                repo_url=project.github_repo_url,
                created=False,
            )

        base_ref = await client.get_ref(owner, repo, base_branch)
        if base_ref is None:
            raise BranchOpError(
                "base_branch_not_found",
                f"base branch {base_branch!r} does not exist on {owner}/{repo}",
            )
        base_sha = base_ref["object"]["sha"]
        created = await client.create_ref(owner, repo, branch_name, base_sha)
        new_sha = created["object"]["sha"]
        logger.info(
            "branch_created",
            request_id=str(request.id),
            branch=branch_name,
            base=base_branch,
            sha=new_sha,
        )
        return BranchInfo(
            name=branch_name,
            sha=new_sha,
            base_branch=base_branch,
            repo_url=project.github_repo_url,
            created=True,
        )
    except GithubNotFound as exc:
        raise BranchOpError("github_unavailable", str(exc)) from exc
    except GithubError as exc:
        if exc.status_code in (401, 403):
            raise BranchOpError("github_auth", str(exc)) from exc
        raise BranchOpError("github_unavailable", str(exc)) from exc
    finally:
        await client.aclose()


async def _resolve_client(factory: GithubClientFactory | None, token: str) -> GithubClient:
    if factory is None:
        return GithubClient(token=token)
    result = factory(token)
    if hasattr(result, "__await__"):
        return await result  # type: ignore[return-value]
    return result  # type: ignore[return-value]
