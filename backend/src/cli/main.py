"""``bsnexus`` Typer entrypoint.

The root app is built by :func:`bsvibe_cli_base.cli_app`, which wires
the standard global flag set (``--profile``, ``--output``, ``--tenant``,
``--token``, ``--url``, ``--dry-run``) and stashes a resolved
:class:`bsvibe_cli_base.CliContext` on ``ctx.obj``. Sub-apps for
projects / requests / decisions / deliverables / events / integrations
are mounted across TASK-003 → TASK-005.
"""

from __future__ import annotations

from bsvibe_cli_base import cli_app

from backend.src.cli.commands import decisions as decisions_app
from backend.src.cli.commands import deliverables as deliverables_app
from backend.src.cli.commands import events as events_app
from backend.src.cli.commands import integrations as integrations_app
from backend.src.cli.commands import projects as projects_app
from backend.src.cli.commands import requests as requests_app

app = cli_app(
    name="bsnexus",
    help="bsnexus — BSNexus admin CLI (control-plane operator surface).",
)

app.add_typer(projects_app.app, name="projects")
app.add_typer(requests_app.app, name="requests")
app.add_typer(decisions_app.app, name="decisions")
app.add_typer(deliverables_app.app, name="deliverables")
app.add_typer(events_app.app, name="events")
app.add_typer(integrations_app.app, name="integrations")


def main() -> None:  # pragma: no cover - thin shim for console_scripts
    app()


__all__ = ["app", "main"]
