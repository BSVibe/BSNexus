"""GitHub REST API client.

Thin httpx wrapper used by ``core/git_ops`` to create branches,
commits, and pull requests. Kept here behind a stable interface so
the rest of the codebase never imports ``httpx`` against
``api.github.com`` directly.
"""

from backend.src.core.github.client import (
    GithubAuthError,
    GithubClient,
    GithubError,
    GithubNotFound,
    GithubValidationError,
    parse_repo_url,
)

__all__ = [
    "GithubAuthError",
    "GithubClient",
    "GithubError",
    "GithubNotFound",
    "GithubValidationError",
    "parse_repo_url",
]
