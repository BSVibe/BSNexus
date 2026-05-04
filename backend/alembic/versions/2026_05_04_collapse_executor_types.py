"""executor_configs.executor_type collapsed to {bsgateway, generic_llm}

Revision ID: k3d4e5f6a7b8
Revises: j2c3d4e5f6a7
Create Date: 2026-05-04 12:00:00.000000

Pre-2026-05-04 the BSNexus ``executor_type`` field exposed BSGateway's
internal model taxonomy (``claude_code`` / ``codex`` / ``opencode`` /
``worker``) directly to BSNexus tenants. That leaked an
implementation detail of the routing layer into BSNexus's data model
and made the ``bsgateway`` vs ``generic_llm`` capability distinction
muddy.

The new taxonomy distinguishes by *infra dependency*:

- ``bsgateway`` — BSVibe infra path. Goes through BSGateway worker
  pool. Model string in ``config.model`` (``claude_code``,
  ``openai/gpt-4o``, etc.) — BSGateway interprets and routes.
- ``generic_llm`` — BSVibe-optional path. Direct LLM call from
  BSNexus via ``core.llm.direct_client`` (litellm + MCP tool loop).
  ``config.model`` is a litellm-style id.

Migration:
- Rows with ``executor_type IN ('claude_code', 'codex', 'opencode',
  'worker')`` → become ``executor_type='bsgateway'``; their old
  ``executor_type`` value is lifted into ``config.model`` (preserving
  any pre-existing ``config.model`` if that key was already set).
- Rows already ``bsgateway`` / ``generic_llm`` → untouched.
- Rows with any other value (none expected, but defensive) →
  forced to ``bsgateway`` with ``config.model`` defaulting to the
  original value if string-shaped.

Idempotent — re-running on an already-migrated DB is a no-op because
the values land in the post-migration shape on the first pass.

Downgrade: drops down to the legacy taxonomy by reading
``config.model`` and writing it back into ``executor_type``. Lossy
for rows that originally had distinct ``executor_type`` and
``config.model`` values; we accept that because the legacy taxonomy
was the bug.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "j2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_MODEL_TYPES = ("claude_code", "codex", "opencode")
_LEGACY_INFRA_TYPES = ("worker",)
# Both ``generic_llm`` (transient name between collapse and the
# 2026-05-04 PM rename) and ``llm_api`` (current canonical) are in
# the pass-through set — keeps this migration forward-compatible if
# it runs against a DB that already saw the rename migration.
_NEW_TYPES = {"bsgateway", "generic_llm", "llm_api"}


def _normalise_config(raw: object) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _collapse_legacy_rows(bind: sa.engine.Connection) -> None:
    """Walk every row and rewrite legacy ``executor_type`` values per the
    docstring-described mapping. Idempotent and dialect-aware (handles
    PG ``CAST AS JSON`` vs SQLite's plain text JSON column).

    Extracted from ``upgrade()`` so unit tests can exercise the lift
    logic directly against an in-memory SQLite without driving alembic.
    """
    is_postgres = bind.dialect.name == "postgresql"

    rows = bind.execute(
        sa.text("SELECT id, executor_type, config FROM executor_configs")
    ).fetchall()

    for row in rows:
        old_type = (row.executor_type or "").lower()
        if old_type in _NEW_TYPES:
            continue

        cfg = _normalise_config(row.config)

        if old_type in _LEGACY_MODEL_TYPES:
            cfg.setdefault("model", old_type)
            new_type = "bsgateway"
        elif old_type in _LEGACY_INFRA_TYPES:
            new_type = "bsgateway"
        else:
            if old_type:
                cfg.setdefault("model", old_type)
            new_type = "bsgateway"

        if is_postgres:
            stmt = sa.text(
                "UPDATE executor_configs "
                "SET executor_type = :t, config = CAST(:cfg AS JSON) "
                "WHERE id = :id"
            )
        else:
            stmt = sa.text(
                "UPDATE executor_configs "
                "SET executor_type = :t, config = :cfg WHERE id = :id"
            )
        bind.execute(stmt, {"id": row.id, "t": new_type, "cfg": json.dumps(cfg)})


def upgrade() -> None:
    _collapse_legacy_rows(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    rows = bind.execute(
        sa.text("SELECT id, executor_type, config FROM executor_configs")
    ).fetchall()
    for row in rows:
        cfg = _normalise_config(row.config)
        model = cfg.get("model")
        if (row.executor_type == "bsgateway") and isinstance(model, str) and model in _LEGACY_MODEL_TYPES:
            # Restore the legacy taxonomy for rows that look like they
            # were lifted from the old type. Strip the model key so the
            # legacy shape is exact.
            cfg.pop("model", None)
            if is_postgres:
                stmt = sa.text(
                    "UPDATE executor_configs "
                    "SET executor_type = :t, config = CAST(:cfg AS JSON) "
                    "WHERE id = :id"
                )
            else:
                stmt = sa.text(
                    "UPDATE executor_configs "
                    "SET executor_type = :t, config = :cfg WHERE id = :id"
                )
            bind.execute(stmt, {"id": row.id, "t": model, "cfg": json.dumps(cfg)})
