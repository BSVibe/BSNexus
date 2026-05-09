"""``bsnexus`` Typer entrypoint."""

from __future__ import annotations

from bsvibe_cli_base import cli_app

# Relative imports — wheel build remaps `src/cli/` → `bsnexus_cli/`, so
# absolute imports of `backend.src.cli.…` only resolve in the source tree.
# Using package-relative names keeps both contexts working.
from .commands import decisions as decisions_app
from .commands import deliverables as deliverables_app
from .commands import projects as projects_app
from .commands import requests as requests_app

app = cli_app(
    name="bsnexus",
    help="bsnexus — BSNexus admin CLI (control-plane operator surface).",
)

app.add_typer(projects_app.app, name="projects")
app.add_typer(requests_app.app, name="requests")
app.add_typer(decisions_app.app, name="decisions")
app.add_typer(deliverables_app.app, name="deliverables")


def main() -> None:  # pragma: no cover - thin shim for console_scripts
    app()


__all__ = ["app", "main"]
