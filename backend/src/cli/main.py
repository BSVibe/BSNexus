"""``bsnexus`` Typer entrypoint.

The root app is built by :func:`bsvibe_cli_base.cli_app`, which wires
the standard global flag set (``--profile``, ``--output``, ``--tenant``,
``--token``, ``--url``, ``--dry-run``) and stashes a resolved
:class:`bsvibe_cli_base.CliContext` on ``ctx.obj``. Sub-apps for
projects / requests / decisions / deliverables / events / integrations
are mounted in TASK-003 through TASK-005.
"""

from __future__ import annotations

from bsvibe_cli_base import cli_app

app = cli_app(
    name="bsnexus",
    help="bsnexus — BSNexus admin CLI (control-plane operator surface).",
)


def main() -> None:  # pragma: no cover - thin shim for console_scripts
    app()


__all__ = ["app", "main"]
