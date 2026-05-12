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


# ──────────────────── Contents API tests (G8.2) ────────────────────


@pytest.mark.asyncio
async def test_get_file_sha_returns_sha_on_200() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = (
            request.url.query.decode("ascii") if isinstance(request.url.query, bytes) else str(request.url.query)
        )
        return httpx.Response(200, json={"sha": "blob-123", "path": "src/api.py"})

    async with _make_client(handler) as client:
        sha = await client.get_file_sha("acme", "widget", "src/api.py", "main")

    assert sha == "blob-123"
    assert captured["path"] == "/repos/acme/widget/contents/src/api.py"
    assert "ref=main" in captured["query"]


@pytest.mark.asyncio
async def test_get_file_sha_returns_none_on_404() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with _make_client(handler) as client:
        sha = await client.get_file_sha("acme", "widget", "missing.py", "main")
    assert sha is None


@pytest.mark.asyncio
async def test_get_file_sha_returns_none_for_directory_response() -> None:
    """Contents API returns a list for directories; we treat that as
    'no single file' and return None so callers don't accidentally
    pass a directory listing as a blob SHA on update.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"sha": "x", "path": "src/a.py"}])

    async with _make_client(handler) as client:
        sha = await client.get_file_sha("acme", "widget", "src", "main")
    assert sha is None


@pytest.mark.asyncio
async def test_put_file_content_creates_file_when_sha_none() -> None:
    import base64

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={
                "content": {"sha": "new-blob", "path": "src/a.py"},
                "commit": {"sha": "commit-1"},
            },
        )

    async with _make_client(handler) as client:
        resp = await client.put_file_content(
            "acme",
            "widget",
            "src/a.py",
            content=b"hello\n",
            message="add a.py",
            branch="bsnexus/req-x",
        )

    assert resp["commit"]["sha"] == "commit-1"
    assert captured["path"] == "/repos/acme/widget/contents/src/a.py"
    body = captured["body"]
    assert isinstance(body, (bytes, bytearray))
    assert b'"branch":"bsnexus/req-x"' in body
    assert b'"message":"add a.py"' in body
    # Content base64-encoded.
    assert base64.b64encode(b"hello\n").decode("ascii").encode("ascii") in body
    # Create path must NOT include a sha key.
    assert b'"sha"' not in body


@pytest.mark.asyncio
async def test_put_file_content_updates_file_when_sha_set() -> None:
    captured_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(request.content)
        return httpx.Response(
            200,
            json={
                "content": {"sha": "new-blob"},
                "commit": {"sha": "commit-2"},
            },
        )

    async with _make_client(handler) as client:
        resp = await client.put_file_content(
            "acme",
            "widget",
            "src/a.py",
            content=b"updated\n",
            message="update a.py",
            branch="bsnexus/req-x",
            sha="old-blob",
        )

    assert resp["commit"]["sha"] == "commit-2"
    assert b'"sha":"old-blob"' in captured_bodies[0]


@pytest.mark.asyncio
async def test_put_file_content_raises_on_409() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "Conflict"})

    async with _make_client(handler) as client:
        with pytest.raises(GithubError) as exc_info:
            await client.put_file_content(
                "acme",
                "widget",
                "src/a.py",
                content=b"x",
                message="m",
                branch="b",
            )
        assert exc_info.value.status_code == 409


# ──────────────────── Pulls API tests (G8.3) ────────────────────


@pytest.mark.asyncio
async def test_list_pulls_filters_state_and_head() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = str(
            request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query
        )
        return httpx.Response(200, json=[{"number": 9, "html_url": "https://x"}])

    async with _make_client(handler) as client:
        prs = await client.list_pulls("acme", "widget", head="bsnexus/req-x", state="open")
    assert len(prs) == 1
    assert prs[0]["number"] == 9
    assert captured["path"] == "/repos/acme/widget/pulls"
    assert "state=open" in captured["query"]
    assert "head=acme%3Absnexus%2Freq-x" in captured["query"]


@pytest.mark.asyncio
async def test_list_pulls_returns_empty_for_no_match() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _make_client(handler) as client:
        prs = await client.list_pulls("acme", "widget", head="missing")
    assert prs == []


@pytest.mark.asyncio
async def test_create_pull_posts_title_head_base_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(
            201,
            json={"number": 17, "html_url": "https://github.com/acme/widget/pull/17", "state": "open"},
        )

    async with _make_client(handler) as client:
        pr = await client.create_pull(
            "acme",
            "widget",
            title="Add /healthz",
            head="bsnexus/req-x",
            base="main",
            body="founder summary here",
        )
    assert pr["number"] == 17
    assert captured["path"] == "/repos/acme/widget/pulls"
    body = captured["body"]
    assert isinstance(body, (bytes, bytearray))
    assert b'"title":"Add /healthz"' in body
    assert b'"head":"bsnexus/req-x"' in body
    assert b'"base":"main"' in body
    assert b'"body":"founder summary here"' in body
    assert b'"draft":false' in body


@pytest.mark.asyncio
async def test_create_pull_raises_on_422_no_commits() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "No commits between main and bsnexus/req-x"})

    async with _make_client(handler) as client:
        with pytest.raises(GithubError) as exc_info:
            await client.create_pull("acme", "widget", title="t", head="bsnexus/req-x", base="main")
    assert exc_info.value.status_code == 422


# ──────────────────── update_pull (G8.4) ────────────────────


@pytest.mark.asyncio
async def test_update_pull_patches_only_provided_fields() -> None:
    captured_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(request.content)
        return httpx.Response(
            200,
            json={"number": 7, "html_url": "https://github.com/acme/widget/pull/7", "state": "open"},
        )

    async with _make_client(handler) as client:
        resp = await client.update_pull("acme", "widget", 7, body="updated body")
    assert resp["number"] == 7
    body = captured_bodies[0]
    assert isinstance(body, (bytes, bytearray))
    assert b'"body":"updated body"' in body
    assert b'"title"' not in body  # title was not passed → not sent


@pytest.mark.asyncio
async def test_update_pull_accepts_title_only() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["path"] = request.url.path
        return httpx.Response(200, json={"number": 7})

    async with _make_client(handler) as client:
        await client.update_pull("acme", "widget", 7, title="New title")
    assert captured["path"] == "/repos/acme/widget/pulls/7"
    body = captured["body"]
    assert isinstance(body, (bytes, bytearray))
    assert b'"title":"New title"' in body
    assert b'"body"' not in body


@pytest.mark.asyncio
async def test_update_pull_raises_on_404() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with _make_client(handler) as client:
        with pytest.raises(GithubNotFound) as exc_info:
            await client.update_pull("acme", "widget", 99, body="x")
    assert exc_info.value.status_code == 404
