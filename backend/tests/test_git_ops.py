"""Tests for backend.src.core.git_ops.GitOps."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.src.core.git_ops import GitOps


@pytest.fixture
def git_ops(tmp_path: object) -> GitOps:
    return GitOps(repo_path=str(tmp_path))


def _make_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> AsyncMock:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# _is_git_repo
# ---------------------------------------------------------------------------


class TestIsGitRepo:
    @pytest.mark.asyncio
    async def test_returns_true_for_valid_repo(self, git_ops: GitOps) -> None:
        proc = _make_proc(returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await git_ops._is_git_repo() is True

    @pytest.mark.asyncio
    async def test_returns_false_for_non_repo(self, git_ops: GitOps) -> None:
        proc = _make_proc(returncode=128)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            assert await git_ops._is_git_repo() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_file_not_found(self, git_ops: GitOps) -> None:
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            assert await git_ops._is_git_repo() is False


# ---------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.asyncio
    async def test_success_returns_stripped_stdout(self, git_ops: GitOps) -> None:
        proc = _make_proc(stdout=b"  hello world  \n", returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await git_ops._run("status", "--short")
            assert result == "hello world"
            # Verify git was called with -C and repo_path
            mock_exec.assert_awaited_once()
            call_args = mock_exec.call_args[0]
            assert call_args[0] == "git"
            assert call_args[1] == "-C"
            assert call_args[2] == git_ops.repo_path
            assert call_args[3] == "status"
            assert call_args[4] == "--short"

    @pytest.mark.asyncio
    async def test_failure_raises_runtime_error(self, git_ops: GitOps) -> None:
        proc = _make_proc(stderr=b"fatal: not a git repository", returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(RuntimeError, match="Git error: fatal: not a git repository"):
                await git_ops._run("status")

    @pytest.mark.asyncio
    async def test_empty_stdout(self, git_ops: GitOps) -> None:
        proc = _make_proc(stdout=b"", returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await git_ops._run("status")
            assert result == ""


# ---------------------------------------------------------------------------
# ensure_repo
# ---------------------------------------------------------------------------


class TestEnsureRepo:
    @pytest.mark.asyncio
    async def test_existing_repo_with_main_branch(self, git_ops: GitOps) -> None:
        """If repo exists and main branch exists, do nothing extra."""
        with (
            patch.object(git_ops, "_is_git_repo", return_value=True),
            patch.object(git_ops, "_run", return_value="") as mock_run,
        ):
            await git_ops.ensure_repo()
            # Should only call rev-parse to verify main
            mock_run.assert_awaited_once_with("rev-parse", "--verify", "main")

    @pytest.mark.asyncio
    async def test_existing_repo_without_main_branch(self, git_ops: GitOps) -> None:
        """If repo exists but main branch missing, create it."""
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            if args == ("rev-parse", "--verify", "main"):
                raise RuntimeError("not found")
            return ""

        with (
            patch.object(git_ops, "_is_git_repo", return_value=True),
            patch.object(git_ops, "_run", side_effect=fake_run),
        ):
            await git_ops.ensure_repo()

        assert ("rev-parse", "--verify", "main") in call_log
        assert ("checkout", "-b", "main") in call_log
        assert ("commit", "--allow-empty", "-m", "chore: initialize repository") in call_log

    @pytest.mark.asyncio
    async def test_new_repo_initialization(self, git_ops: GitOps) -> None:
        """If not a git repo, initialize from scratch."""
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            return ""

        with (
            patch.object(git_ops, "_is_git_repo", return_value=False),
            patch.object(git_ops, "_run", side_effect=fake_run),
            patch("backend.src.core.git_ops.Path") as mock_path_cls,
        ):
            mock_path_inst = MagicMock()
            mock_path_cls.return_value = mock_path_inst
            await git_ops.ensure_repo()

        mock_path_inst.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        assert ("init", git_ops.repo_path) in call_log
        assert ("config", "user.email", "bsnexus@localhost") in call_log
        assert ("config", "user.name", "BSNexus") in call_log
        assert ("checkout", "-b", "main") in call_log
        assert ("commit", "--allow-empty", "-m", "chore: initialize repository") in call_log


# ---------------------------------------------------------------------------
# ensure_branch
# ---------------------------------------------------------------------------


class TestEnsureBranch:
    @pytest.mark.asyncio
    async def test_branch_exists_checks_out(self, git_ops: GitOps) -> None:
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            if args[:2] == ("branch", "--list"):
                return "  feature-1"
            return ""

        with patch.object(git_ops, "_run", side_effect=fake_run):
            await git_ops.ensure_branch("feature-1")

        assert ("checkout", "feature-1") in call_log

    @pytest.mark.asyncio
    async def test_branch_not_exists_creates_from_main(self, git_ops: GitOps) -> None:
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            if args[:2] == ("branch", "--list"):
                return ""
            return ""

        with patch.object(git_ops, "_run", side_effect=fake_run):
            await git_ops.ensure_branch("feature-2")

        assert ("rev-parse", "--verify", "main") in call_log
        assert ("checkout", "-b", "feature-2", "main") in call_log

    @pytest.mark.asyncio
    async def test_branch_not_exists_no_main_creates_orphan(self, git_ops: GitOps) -> None:
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            if args[:2] == ("branch", "--list"):
                return ""
            if args == ("rev-parse", "--verify", "main"):
                raise RuntimeError("main not found")
            return ""

        with patch.object(git_ops, "_run", side_effect=fake_run):
            await git_ops.ensure_branch("feature-3")

        assert ("checkout", "-b", "feature-3") in call_log


# ---------------------------------------------------------------------------
# commit_task
# ---------------------------------------------------------------------------


class TestCommitTask:
    @pytest.mark.asyncio
    async def test_has_changes_commits_and_returns_hash(self, git_ops: GitOps) -> None:
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            if args == ("diff", "--cached", "--quiet"):
                raise RuntimeError("changes exist")
            if args == ("rev-parse", "HEAD"):
                return "abc123def456\n"
            return ""

        with (
            patch.object(git_ops, "ensure_branch", new_callable=AsyncMock),
            patch.object(git_ops, "_run", side_effect=fake_run),
        ):
            result = await git_ops.commit_task("42", "implement feature", "feature-branch")

        assert result == "abc123def456"
        assert ("add", ".") in call_log
        assert ("commit", "-m", "feat(task-42): implement feature") in call_log

    @pytest.mark.asyncio
    async def test_no_changes_returns_empty_string(self, git_ops: GitOps) -> None:
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            return ""

        with (
            patch.object(git_ops, "ensure_branch", new_callable=AsyncMock),
            patch.object(git_ops, "_run", side_effect=fake_run),
        ):
            result = await git_ops.commit_task("42", "implement feature", "feature-branch")

        assert result == ""
        # Should not have called commit
        assert ("commit", "-m", "feat(task-42): implement feature") not in call_log

    @pytest.mark.asyncio
    async def test_ensure_branch_called_with_correct_name(self, git_ops: GitOps) -> None:
        async def fake_run(*args: str) -> str:
            return ""

        with (
            patch.object(git_ops, "ensure_branch", new_callable=AsyncMock) as mock_branch,
            patch.object(git_ops, "_run", side_effect=fake_run),
        ):
            await git_ops.commit_task("1", "title", "my-branch")

        mock_branch.assert_awaited_once_with("my-branch")


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_invokes_git_status_short(self, git_ops: GitOps) -> None:
        with patch.object(git_ops, "_run", return_value="M  file.py\n?? new.py") as mock_run:
            result = await git_ops.get_status()
            mock_run.assert_awaited_once_with("status", "--short")
            assert "M  file.py" in result
            assert "?? new.py" in result

    @pytest.mark.asyncio
    async def test_returns_empty_for_clean_repo(self, git_ops: GitOps) -> None:
        with patch.object(git_ops, "_run", return_value=""):
            result = await git_ops.get_status()
            assert result == ""


# ---------------------------------------------------------------------------
# revert_task
# ---------------------------------------------------------------------------


class TestRevertTask:
    @pytest.mark.asyncio
    async def test_with_commit_hash_calls_revert(self, git_ops: GitOps) -> None:
        call_log: list[tuple[str, ...]] = []

        async def fake_run(*args: str) -> str:
            call_log.append(args)
            return ""

        with (
            patch.object(git_ops, "ensure_branch", new_callable=AsyncMock) as mock_branch,
            patch.object(git_ops, "_run", side_effect=fake_run),
        ):
            await git_ops.revert_task("abc123", "feature-branch")

        mock_branch.assert_awaited_once_with("feature-branch")
        assert ("revert", "--no-edit", "abc123") in call_log

    @pytest.mark.asyncio
    async def test_empty_hash_is_noop(self, git_ops: GitOps) -> None:
        with (
            patch.object(git_ops, "ensure_branch", new_callable=AsyncMock) as mock_branch,
            patch.object(git_ops, "_run", new_callable=AsyncMock) as mock_run,
        ):
            await git_ops.revert_task("", "feature-branch")

        mock_branch.assert_not_awaited()
        mock_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_hash_is_noop(self, git_ops: GitOps) -> None:
        with (
            patch.object(git_ops, "ensure_branch", new_callable=AsyncMock) as mock_branch,
            patch.object(git_ops, "_run", new_callable=AsyncMock) as mock_run,
        ):
            await git_ops.revert_task("", "branch")

        mock_branch.assert_not_awaited()
        mock_run.assert_not_awaited()


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_repo_path(self) -> None:
        ops = GitOps(repo_path="/some/path")
        assert ops.repo_path == "/some/path"
