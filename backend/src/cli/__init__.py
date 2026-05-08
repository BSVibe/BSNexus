"""``bsnexus`` Typer CLI built on ``bsvibe-cli-base``.

Sub-apps mounted in TASK-003+ wrap the existing REST routers in
:mod:`backend.src.api`. Each sub-command consumes the shared
:class:`bsvibe_cli_base.CliContext` from ``ctx.obj`` and uses
:func:`backend.src.cli._client.build_http_client` to obtain a
configured :class:`bsvibe_cli_base.CliHttpClient`.

Note: the wheel ships this package as top-level ``bsnexus_cli`` (see
``[tool.hatch.build.targets.wheel.force-include]`` in
``backend/pyproject.toml``). This module deliberately does not
re-export ``app`` so the source path and the wheel path resolve the
same way.
"""
