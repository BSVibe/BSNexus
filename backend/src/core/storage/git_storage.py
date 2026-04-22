"""GitStorage — wraps core.git_ops for code deliverables.

Content is written to the project's git workspace, committed on a
per-run branch, and referenced by (repo, commit, path) tuple.

Unlike S3/Local, ``put`` commits immediately (one commit per version).
Downstream consumers use the commit hash to recover the content.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from backend.src.core.git_ops import GitOps
from backend.src.core.storage.deliverable_storage import ContentRef
from backend.src.models import StorageBackend


async def _iter_to_bytes(content: AsyncIterator[bytes] | bytes) -> bytes:
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    chunks: list[bytes] = []
    async for chunk in content:
        chunks.append(chunk)
    return b"".join(chunks)


class GitStorage:
    backend = StorageBackend.git

    def __init__(self, repo_path: str, *, branch_prefix: str = "run/"):
        self._repo_path = repo_path
        self._branch_prefix = branch_prefix
        self._git = GitOps(repo_path)

    async def put(
        self,
        key: str,
        content: AsyncIterator[bytes] | bytes,
        *,
        metadata: dict | None = None,
    ) -> ContentRef:
        """Write ``content`` to ``key`` within the repo and commit.

        ``metadata`` may carry ``branch`` (to reuse a run's branch) and
        ``commit_message``. Defaults are sensible for a one-run-one-
        version flow.
        """
        meta = dict(metadata or {})
        branch = meta.get("branch", f"{self._branch_prefix}pending")
        commit_message = meta.get("commit_message", f"chore: write {key}")

        await self._git.ensure_repo()
        await self._git.ensure_branch(branch)

        data = await _iter_to_bytes(content)
        target = Path(self._repo_path) / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        commit_hash = await self._git.commit_task(
            task_id=meta.get("run_id", "unknown"),
            title=commit_message,
            branch_name=branch,
        )

        return ContentRef(
            backend=StorageBackend.git,
            data={
                "repo": self._repo_path,
                "commit": commit_hash,
                "path": key,
                "branch": branch,
            },
        )

    async def get(self, ref: ContentRef) -> AsyncIterator[bytes]:
        path = Path(ref.data["repo"]) / ref.data["path"]
        data = path.read_bytes()

        async def _gen() -> AsyncIterator[bytes]:
            yield data

        return _gen()

    async def presign(self, ref: ContentRef, ttl_s: int = 3600) -> str:
        """Git content is not web-presigned. Return a repo-local reference."""
        return (
            f"git://{ref.data['repo']}#"
            f"commit={ref.data.get('commit', '')};"
            f"path={ref.data['path']}"
        )

    async def delete(self, ref: ContentRef) -> None:
        """Intentionally a no-op.

        Git deliverables are immutable history — committing a "delete"
        means a new revert commit, not erasing prior versions. Callers
        that really want to delete should commit a new empty version
        via ``put``.
        """
        return None
