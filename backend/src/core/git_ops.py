import asyncio
import structlog
from pathlib import Path

logger = structlog.get_logger(__name__)


class GitOps:
    """Git automation for local task execution.

    Handles repo initialization, branch management, task commits, and reverts.
    All operations are idempotent and safe to call multiple times.
    """

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path

    async def ensure_repo(self) -> None:
        """Initialize git repo if it doesn't exist."""
        if await self._is_git_repo():
            try:
                await self._run("rev-parse", "--verify", "main")
            except RuntimeError:
                await self._run("checkout", "-b", "main")
                await self._run("commit", "--allow-empty", "-m", "chore: initialize repository")
            return
        Path(self.repo_path).mkdir(parents=True, exist_ok=True)
        await self._run("init", self.repo_path)
        await self._run("config", "user.email", "bsnexus@localhost")
        await self._run("config", "user.name", "BSNexus")
        await self._run("checkout", "-b", "main")
        await self._run("commit", "--allow-empty", "-m", "chore: initialize repository")

    async def ensure_branch(self, branch_name: str) -> None:
        """Check out branch, creating it from main if it doesn't exist."""
        branches = await self._run("branch", "--list", branch_name)
        # git branch --list output has "* " prefix for current branch and "  " for others
        branch_names = [line.lstrip("* ").strip() for line in branches.splitlines()]
        if branch_name in branch_names:
            await self._run("checkout", branch_name)
        else:
            try:
                await self._run("rev-parse", "--verify", "main")
                await self._run("checkout", "-b", branch_name, "main")
            except RuntimeError:
                await self._run("checkout", "-b", branch_name)

    async def commit_task(self, task_id: str, title: str, branch_name: str) -> str:
        """Stage all changes and commit. Returns commit hash, or empty string if no changes."""
        await self.ensure_branch(branch_name)
        await self._run("add", ".")
        try:
            await self._run("diff", "--cached", "--quiet")
            return ""  # nothing to commit
        except RuntimeError:
            pass  # staged changes exist
        safe_title = title.replace("\n", " ").replace("\r", " ").strip()[:100]
        message = f"feat(task-{task_id}): {safe_title}"
        await self._run("commit", "-m", message)
        return (await self._run("rev-parse", "HEAD")).strip()

    async def get_status(self) -> str:
        """Get short git status output."""
        return await self._run("status", "--short")

    async def revert_task(self, commit_hash: str, branch_name: str) -> None:
        """Revert a specific commit on the given branch."""
        if not commit_hash:
            return
        await self.ensure_branch(branch_name)
        await self._run("revert", "--no-edit", commit_hash)

    # ── Remote operations (GitHub) ─────────────────────────────────

    async def clone(self, url: str, branch: str = "main") -> None:
        """Clone a remote repo into repo_path."""
        await asyncio.create_subprocess_exec(
            "git", "clone", "--branch", branch, "--single-branch", url, self.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Configure identity
        await self._run("config", "user.email", "bsnexus@localhost")
        await self._run("config", "user.name", "BSNexus")

    async def add_remote(self, name: str, url: str) -> None:
        """Add or update a git remote."""
        try:
            await self._run("remote", "get-url", name)
            await self._run("remote", "set-url", name, url)
        except RuntimeError:
            await self._run("remote", "add", name, url)

    async def pull(self, remote: str = "origin", branch: str = "main") -> str:
        """Pull from remote. Returns output."""
        return await self._run("pull", remote, branch, "--ff-only")

    async def push(self, remote: str = "origin", branch: str = "main") -> str:
        """Push to remote. Returns output."""
        return await self._run("push", remote, branch)

    async def has_remote(self, name: str = "origin") -> bool:
        """Check if a remote exists."""
        try:
            await self._run("remote", "get-url", name)
            return True
        except RuntimeError:
            return False

    async def _is_git_repo(self) -> bool:
        """Check if repo_path is an existing git repository."""
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                self.repo_path,
                "rev-parse",
                "--git-dir",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            return process.returncode == 0
        except FileNotFoundError:
            return False

    async def _run(self, *args: str) -> str:
        """Execute a git command asynchronously."""
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            self.repo_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"Git error: {stderr.decode(errors='replace')}")

        return stdout.decode(errors="replace").strip()
