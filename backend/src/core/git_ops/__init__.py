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

__all__ = [
    "BranchInfo",
    "BranchOpError",
    "build_request_branch_name",
    "ensure_request_branch",
]
