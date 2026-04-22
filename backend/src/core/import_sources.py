"""Pluggable source providers for the import-project flow.

A source knows how to materialize an existing codebase into a working
directory so the analyzer worker can read it. The choice is up to the
user when they kick off an import:

  - LocalPathSource: copy from a path on the host running BSNexus
  - GitRemoteSource: shallow clone an https/ssh URL
  - TarballSource: extract an uploaded tar/zip archive

Each provider is a small async coroutine returning a ``RepoMetadata``
record. They are intentionally side-effect-light: they only populate
the target directory and report what they found.
"""

from __future__ import annotations

import asyncio
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class RepoMetadata:
    """Lightweight description of what landed in the workspace."""

    workspace_dir: Path
    files_count: int
    has_git: bool
    detected_language: str | None = None


class ImportSource(Protocol):
    """Async interface every source provider must satisfy."""

    async def fetch(self, target: Path) -> RepoMetadata: ...


# ── Concrete providers ──────────────────────────────────────────────


class LocalPathSource:
    """Copy a directory from the host filesystem into the workspace."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)

    async def fetch(self, target: Path) -> RepoMetadata:
        src = self.repo_path
        if not src.exists() or not src.is_dir():
            raise FileNotFoundError(f"Source path does not exist or is not a directory: {src}")
        if target.exists():
            shutil.rmtree(target)
        await asyncio.to_thread(
            shutil.copytree, src, target, ignore=shutil.ignore_patterns(".venv", "node_modules")
        )
        return _summarize(target)


class GitRemoteSource:
    """Shallow-clone a git URL into the workspace."""

    def __init__(self, url: str, branch: str | None = None) -> None:
        self.url = url
        self.branch = branch

    async def fetch(self, target: Path) -> RepoMetadata:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1"]
        if self.branch:
            cmd += ["--branch", self.branch]
        cmd += [self.url, str(target)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {stderr.decode().strip()}")
        return _summarize(target)


class TarballSource:
    """Extract a previously uploaded tar.gz or zip archive into the workspace."""

    def __init__(self, archive_path: str) -> None:
        self.archive_path = Path(archive_path)

    async def fetch(self, target: Path) -> RepoMetadata:
        if not self.archive_path.exists():
            raise FileNotFoundError(f"Archive not found: {self.archive_path}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        suffix = "".join(self.archive_path.suffixes).lower()
        if suffix.endswith(".zip"):
            await asyncio.to_thread(_extract_zip, self.archive_path, target)
        elif suffix.endswith((".tar.gz", ".tgz", ".tar")):
            await asyncio.to_thread(_extract_tar, self.archive_path, target)
        else:
            raise ValueError(f"Unsupported archive format: {self.archive_path}")
        return _summarize(target)


# ── Helpers ─────────────────────────────────────────────────────────


def _extract_zip(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)


def _extract_tar(archive: Path, target: Path) -> None:
    with tarfile.open(archive) as tf:
        tf.extractall(target)


def _summarize(workspace_dir: Path) -> RepoMetadata:
    files = [p for p in workspace_dir.rglob("*") if p.is_file() and ".git" not in p.parts]
    has_git = (workspace_dir / ".git").exists()
    detected = _detect_language(files)
    return RepoMetadata(
        workspace_dir=workspace_dir,
        files_count=len(files),
        has_git=has_git,
        detected_language=detected,
    )


def _detect_language(files: list[Path]) -> str | None:
    if not files:
        return None
    counts: dict[str, int] = {}
    for f in files:
        ext = f.suffix.lower()
        counts[ext] = counts.get(ext, 0) + 1
    extension_to_lang = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".rb": "ruby",
        ".kt": "kotlin",
        ".swift": "swift",
    }
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    for ext, _ in ranked:
        if ext in extension_to_lang:
            return extension_to_lang[ext]
    return None
