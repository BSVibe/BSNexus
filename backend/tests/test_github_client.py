"""Unit tests for ``core/github/client.py`` (G8.1).

Uses ``httpx.MockTransport`` so the tests never hit the real GitHub
API. The mock validates the request shape (path, headers, body) and
returns canned responses; this is the contract we depend on, so
record it in the tests rather than only in code comments.
"""

from __future__ import annotations

import httpx
import pytest

from backend.src.core.github.client import (
    GithubAuthError,
    GithubClient,
    GithubError,
    GithubNotFound,
    parse_repo_url,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/acme/widget", ("acme", "widget")),
        ("https://github.com/acme/widget.git", ("acme", "widget")),
        ("https://github.com/acme/widget/", ("acme", "widget")),
        ("https://github.com/Acme/Widget-Repo", ("Acme", "Widget-Repo")),
    ],
)
def test_parse_repo_url_accepts_github_https(url: str, expected: tuple[str, str]) -> None:
    assert parse_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:acme/widget.git",
        "ssh://git@github.com/acme/widget",
        "https://example.com/acme/widget",
        "",
        "https://github.com/acme",
        "https://github.com/",
    ],
)
def test_parse_repo_url_rejects_non_github_https(url: str) -> None:
    with pytest.raises(ValueError):
        parse_repo_url(url)


def _make_client(handler) -> GithubClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="https://api.github.com", transport=transport)
    return GithubClient(token="tok", client=http)


@pytest.mark.asyncio
async def test_get_ref_returns_dict_on_200() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={
                "ref": "refs/heads/main",
                "object": {"sha": "abc123", "type": "commit"},
            },
        )

    async with _make_client(handler) as client:
        ref = await client.get_ref("acme", "widget", "main")

    assert ref is not None
    assert ref["object"]["sha"] == "abc123"
    assert captured["path"] == "/repos/acme/widget/git/refs/heads/main"
    assert captured["auth"] == "Bearer tok"


@pytest.mark.asyncio
async def test_get_ref_returns_none_on_404() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with _make_client(handler) as client:
        ref = await client.get_ref("acme", "widget", "ghost-branch")
    assert ref is None


@pytest.mark.asyncio
async def test_get_ref_raises_auth_error_on_401() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    async with _make_client(handler) as client:
        with pytest.raises(GithubAuthError) as exc_info:
            await client.get_ref("acme", "widget", "main")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_create_ref_posts_correct_body_and_returns_201() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={
                "ref": "refs/heads/bsnexus/req-x",
                "object": {"sha": "deadbeef", "type": "commit"},
            },
        )

    async with _make_client(handler) as client:
        ref = await client.create_ref("acme", "widget", "bsnexus/req-x", "abc123")
    assert ref["object"]["sha"] == "deadbeef"
    assert captured["path"] == "/repos/acme/widget/git/refs"
    assert b'"refs/heads/bsnexus/req-x"' in captured["body"]
    assert b'"abc123"' in captured["body"]


@pytest.mark.asyncio
async def test_create_ref_is_idempotent_on_422_existing() -> None:
    """If the branch already exists, GitHub responds 422; the client
    re-fetches and returns the existing ref shape rather than raising.
    """
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(422, json={"message": "Reference already exists"})
        return httpx.Response(
            200,
            json={
                "ref": "refs/heads/bsnexus/req-x",
                "object": {"sha": "feedface", "type": "commit"},
            },
        )

    async with _make_client(handler) as client:
        ref = await client.create_ref("acme", "widget", "bsnexus/req-x", "abc123")
    assert ref["object"]["sha"] == "feedface"
    assert ("POST", "/repos/acme/widget/git/refs") in calls
    assert ("GET", "/repos/acme/widget/git/refs/heads/bsnexus/req-x") in calls


@pytest.mark.asyncio
async def test_create_ref_raises_other_4xx() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "Conflict"})

    async with _make_client(handler) as client:
        with pytest.raises(GithubError) as exc_info:
            await client.create_ref("acme", "widget", "bsnexus/req-x", "abc123")
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_get_ref_5xx_raises_generic_github_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "Service Unavailable"})

    async with _make_client(handler) as client:
        with pytest.raises(GithubError) as exc_info:
            await client.get_ref("acme", "widget", "main")
        assert exc_info.value.status_code == 503
        # Not the more specific subclasses.
        assert not isinstance(exc_info.value, (GithubAuthError, GithubNotFound))
