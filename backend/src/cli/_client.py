"""HTTP client factory for ``bsnexus`` sub-commands.

Centralises the :class:`bsvibe_cli_base.CliHttpClient` construction so
every sub-app pulls the same shape: base URL from the active profile
(or ``--url`` override), bearer token from keyring/env/profile (or
``--token`` override), and an optional ``X-Tenant-Id`` header when the
caller passed ``--tenant`` / has a tenant pinned on the profile.

The factory does not own dry-run behaviour. ``CliContext.dry_run`` is
inspected by each sub-command before any network call so that the
client is never constructed for a dry run that doesn't need it.
"""

from __future__ import annotations

from bsvibe_cli_base import CliContext, CliHttpClient


def build_http_client(ctx: CliContext) -> CliHttpClient:
    """Build a :class:`CliHttpClient` from a resolved :class:`CliContext`.

    Raises :class:`ValueError` if no base URL was resolved — sub-commands
    surface a friendly message before reaching this code path, but the
    explicit guard keeps test failures sharp.
    """

    if not ctx.url:
        raise ValueError("No control-plane base URL configured. Pass --url or set one on the active profile.")
    headers: dict[str, str] = {}
    if ctx.tenant_id:
        headers["X-Tenant-Id"] = ctx.tenant_id
    return CliHttpClient(
        base_url=ctx.url,
        token=ctx.token,
        headers=headers or None,
    )


__all__ = ["build_http_client"]
