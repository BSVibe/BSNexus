"""GitHub REST API client (G8.1).

Surfaces the subset BSNexus needs for repo-native delivery:

  - parse_repo_url(url) → (owner, repo)
  - get_ref(owner, repo, branch) → ref dict or None
  - create_ref(owner, repo, branch, sha) → ref dict (idempotent on 422)

The client is constructed per-call with a decrypted PAT; the resolver
in ``core/git_ops`` handles encryption + tenant scoping. There is no
process-wide singleton — multiple tenants must not share an httpx
client + auth header.
"""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


class GithubError(Exception):
    """Base class for ``GithubClient`` errors. ``status_code`` is the
    upstream HTTP status, or ``None`` for transport-layer failures.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GithubAuthError(GithubError):
    """401/403 — bad token, insufficient scopes, or rate-limit."""


class GithubNotFound(GithubError):
    """404 — repo or ref does not exist."""


class GithubValidationError(GithubError):
    """422 — request body was rejected. For create_ref this typically
    means the ref already exists; ``create_ref`` handles that case
    transparently rather than raising.
    """


_HTTPS_REPO_RE = re.compile(r"^https://(?:[^/]+\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def parse_repo_url(url: str) -> tuple[str, str]:
    """Extract ``(owner, repo)`` from an https://github.com URL.

    Accepts ``https://github.com/acme/widget`` and the ``.git`` suffix
    variant. Subdomains (e.g. GitHub Enterprise on a custom host) are
    rejected — Enterprise support is a separate spec line.
    """
    match = _HTTPS_REPO_RE.match(url.strip())
    if not match:
        raise ValueError(f"unsupported repo url: {url!r}")
    owner = match.group("owner")
    repo = match.group("repo")
    if not owner or not repo:
        raise ValueError(f"unsupported repo url: {url!r}")
    return owner, repo


class GithubClient:
    """Async httpx wrapper. One instance per call site — do not cache."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_s: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)
        # Apply auth + content headers regardless of whether the client
        # was injected — the test transport reads them off the request,
        # not just off the constructor.
        self._client.headers["Accept"] = "application/vnd.github+json"
        self._client.headers["X-GitHub-Api-Version"] = "2022-11-28"
        self._client.headers["User-Agent"] = "bsnexus/0.1"
        if token:
            self._client.headers["Authorization"] = f"Bearer {token}"

    async def __aenter__(self) -> GithubClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_ref(self, owner: str, repo: str, branch: str) -> dict[str, Any] | None:
        """``GET /repos/{owner}/{repo}/git/refs/heads/{branch}`` — returns
        the ref dict (with ``object.sha``), or ``None`` when GitHub
        responds 404.
        """
        resp = await self._client.get(f"/repos/{owner}/{repo}/git/refs/heads/{branch}")
        if resp.status_code == 404:
            return None
        self._raise_for_status(resp, action="get_ref")
        return resp.json()

    async def create_ref(self, owner: str, repo: str, branch: str, sha: str) -> dict[str, Any]:
        """``POST /repos/{owner}/{repo}/git/refs`` — create a new branch
        ref pointing at ``sha``. If the ref already exists, GitHub
        responds 422; we transparently re-fetch and return the existing
        ref so the call is idempotent.
        """
        body = {"ref": f"refs/heads/{branch}", "sha": sha}
        resp = await self._client.post(f"/repos/{owner}/{repo}/git/refs", json=body)
        if resp.status_code == 422:
            existing = await self.get_ref(owner, repo, branch)
            if existing is not None:
                return existing
            # 422 but the ref isn't there — surface the original error.
            self._raise_for_status(resp, action="create_ref")
        self._raise_for_status(resp, action="create_ref")
        return resp.json()

    async def get_file_sha(self, owner: str, repo: str, path: str, branch: str) -> str | None:
        """``GET /repos/{owner}/{repo}/contents/{path}?ref={branch}``.

        Returns the file's blob SHA when it exists on ``branch``, or
        ``None`` when the path is absent. Needed by ``put_file_content``
        to update an existing file (GitHub requires the prior SHA on
        update; PUT without SHA is "create only").
        """
        resp = await self._client.get(f"/repos/{owner}/{repo}/contents/{path}", params={"ref": branch})
        if resp.status_code == 404:
            return None
        self._raise_for_status(resp, action="get_file_sha")
        body = resp.json()
        # The contents endpoint returns a list for directories; we
        # only treat single-file responses as a hit.
        if isinstance(body, dict):
            sha = body.get("sha")
            return str(sha) if isinstance(sha, str) else None
        return None

    async def list_pulls(
        self, owner: str, repo: str, *, head: str | None = None, state: str = "open"
    ) -> list[dict[str, Any]]:
        """``GET /repos/{owner}/{repo}/pulls`` filtered by ``state`` and
        optionally ``head`` (GitHub expects ``owner:branch``; we accept
        the bare branch and prepend ``owner:``).
        """
        params: dict[str, str] = {"state": state}
        if head:
            params["head"] = f"{owner}:{head}"
        resp = await self._client.get(f"/repos/{owner}/{repo}/pulls", params=params)
        self._raise_for_status(resp, action="list_pulls")
        body = resp.json()
        return list(body) if isinstance(body, list) else []

    async def create_pull(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        """``POST /repos/{owner}/{repo}/pulls`` — open a PR from
        ``head`` against ``base``. Returns the GitHub PR object
        (``number``, ``html_url``, ``state``, ...).
        """
        payload: dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": draft,
        }
        resp = await self._client.post(f"/repos/{owner}/{repo}/pulls", json=payload)
        self._raise_for_status(resp, action="create_pull")
        return resp.json()

    async def update_pull(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """``PATCH /repos/{owner}/{repo}/pulls/{number}`` — update an
        existing PR's title and/or body. Only fields with a non-None
        value are sent; pass ``body=""`` to explicitly clear the body.
        """
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        resp = await self._client.patch(f"/repos/{owner}/{repo}/pulls/{number}", json=payload)
        self._raise_for_status(resp, action="update_pull")
        return resp.json()

    async def put_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        *,
        content: bytes,
        message: str,
        branch: str,
        sha: str | None = None,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> dict[str, Any]:
        """``PUT /repos/{owner}/{repo}/contents/{path}`` — create or
        update a file on ``branch``. ``sha`` MUST be the file's prior
        blob SHA when updating, MUST be omitted when creating; callers
        can pre-resolve via ``get_file_sha`` to make the call uniform.

        Returns the GitHub response body which contains ``commit.sha``
        for the new commit.
        """
        body: dict[str, Any] = {
            "message": message,
            "branch": branch,
            "content": base64.b64encode(content).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        if author_name and author_email:
            body["committer"] = {"name": author_name, "email": author_email}
        resp = await self._client.put(f"/repos/{owner}/{repo}/contents/{path}", json=body)
        self._raise_for_status(resp, action="put_file_content")
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: httpx.Response, *, action: str) -> None:
        status = resp.status_code
        if status < 400:
            return
        try:
            body = resp.json()
        except ValueError:
            body = {"message": resp.text[:200]}
        message = str(body.get("message") or body)
        logger.warning("github_api_error", action=action, status_code=status, message=message)
        if status in (401, 403):
            raise GithubAuthError(f"{action}: {message}", status_code=status)
        if status == 404:
            raise GithubNotFound(f"{action}: {message}", status_code=status)
        if status == 422:
            raise GithubValidationError(f"{action}: {message}", status_code=status)
        raise GithubError(f"{action}: {message}", status_code=status)
