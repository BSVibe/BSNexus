"""Repo-native delivery operations (G8.1+).

Higher-level helpers that combine ``core/github`` REST calls with
BSNexus model state. Callers in ``api/`` and the dispatcher use these
functions instead of touching the GitHub client directly.
"""

from backend.src.core.git_ops.branch import (
    BranchInfo,
    BranchOpError,
    build_request_branch_name,
    ensure_request_branch,
)
from backend.src.core.git_ops.commit import (
    CommitOpError,
    CommitResult,
    commit_deliverable,
)
from backend.src.core.git_ops.pull_request import (
    PullRequestInfo,
    PullRequestOpError,
    open_request_pr,
)

__all__ = [
    "BranchInfo",
    "BranchOpError",
    "CommitOpError",
    "CommitResult",
    "PullRequestInfo",
    "PullRequestOpError",
    "build_request_branch_name",
    "commit_deliverable",
    "ensure_request_branch",
    "open_request_pr",
]
