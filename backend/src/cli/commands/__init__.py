"""``bsnexus`` CLI sub-app modules.

Each module exposes a Typer ``app`` mounted in
:mod:`backend.src.cli.main`. They share the small helper module
:mod:`backend.src.cli.commands._common` for dry-run rendering, friendly
HTTP error printing, and the async coroutine runner.
"""
