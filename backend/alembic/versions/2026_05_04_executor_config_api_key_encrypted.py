"""executor_configs.api_key_encrypted column + plaintext data migration

Revision ID: j2c3d4e5f6a7
Revises: i1b2c3d4e5f6
Create Date: 2026-05-04 00:00:00.000000

Closes the PR #42 #11 follow-up: ``ExecutorConfig.config`` was a plain
JSON column that stored ``bsgateway_api_key`` / ``api_key`` plaintext.
Anyone with read access to the DB (operator, backup tape, replica)
could read the BSGateway service-token directly. Mirror the encryption
pattern already in use on
``tenant_integration_configs.api_key_encrypted``.

Migration is **idempotent + safe-to-rerun**:
- ADD COLUMN api_key_encrypted TEXT NULL
- For each row where ``config`` carries ``bsgateway_api_key`` / ``api_key``:
  encrypt the value with ``EncryptionManager(settings.encryption_key)``
  → write to ``api_key_encrypted`` → strip both keys from the
  ``config`` JSON object so plaintext doesn't linger in old rows.
- Rows already migrated (api_key_encrypted populated, config keys
  absent) are left alone.

Downgrade: drop the column. Plaintext is **not** restored — operator
must re-enter the secret via the API. We refuse to write decrypted
values back into ``config`` because that defeats the purpose of the
upgrade.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "i1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SENSITIVE_KEYS = ("bsgateway_api_key", "api_key")


def _migrate_plaintext_rows(bind: sa.engine.Connection) -> None:
    rows = bind.execute(sa.text("SELECT id, config FROM executor_configs")).fetchall()
    if not rows:
        return

    # Local import — settings + EncryptionManager aren't at module top
    # so a fresh-PG smoke test that parses every migration file at
    # collection time stays decoupled from the app's runtime config.
    from backend.src.config import settings  # noqa: PLC0415
    from backend.src.core.encryption import EncryptionManager  # noqa: PLC0415

    enc = EncryptionManager(settings.encryption_key)
    is_postgres = bind.dialect.name == "postgresql"

    for row in rows:
        cfg = row.config
        # SQLite returns dict via the JSON adapter; some PG drivers return
        # a serialised string. Normalise both.
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except (ValueError, TypeError):
                continue
        if not isinstance(cfg, dict):
            continue

        plaintext = None
        for key in _SENSITIVE_KEYS:
            value = cfg.get(key)
            if isinstance(value, str) and value:
                plaintext = value
                break
        if plaintext is None:
            continue

        encrypted = enc.encrypt_value(plaintext)
        cleaned = {k: v for k, v in cfg.items() if k not in _SENSITIVE_KEYS}

        # PG's JSON column wants a json-typed bind parameter; SQLite
        # accepts the JSON-string directly through its JSON adapter. The
        # cast on PG is applied via the column type so we just bind the
        # serialised string and let the dialect adapt.
        if is_postgres:
            stmt = sa.text(
                "UPDATE executor_configs "
                "SET api_key_encrypted = :enc, "
                "    config = CAST(:cfg AS JSON) "
                "WHERE id = :id"
            )
        else:
            stmt = sa.text(
                "UPDATE executor_configs "
                "SET api_key_encrypted = :enc, config = :cfg "
                "WHERE id = :id"
            )
        bind.execute(
            stmt,
            {"id": row.id, "enc": encrypted, "cfg": json.dumps(cleaned)},
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("executor_configs")}

    if "api_key_encrypted" not in existing_cols:
        op.add_column(
            "executor_configs",
            sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        )

    _migrate_plaintext_rows(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("executor_configs")}
    if "api_key_encrypted" in existing_cols:
        with op.batch_alter_table("executor_configs") as batch:
            batch.drop_column("api_key_encrypted")
