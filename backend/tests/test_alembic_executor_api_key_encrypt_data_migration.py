"""Data-migration coverage for the
``2026_05_04_executor_config_api_key_encrypted`` Alembic step.

The fresh-PG smoke test (``test_alembic_fresh_migration``) only proves
the migration applies cleanly to an empty DB. It says nothing about the
*data migration* — the part that takes legacy rows whose
``config["bsgateway_api_key"]`` carries plaintext and encrypts them
into the new column.

This test exercises the migration's helper directly against a SQLite
schema that simulates a row in the pre-2026-05-04 shape (column
absent, plaintext in config). It pins:

1. After ``_migrate_plaintext_rows``, the row's ``api_key_encrypted``
   is non-empty and decrypts back to the original plaintext.
2. The plaintext key is removed from ``config`` JSON.
3. Other ``config`` keys (``bsgateway_url`` etc.) survive.
4. Rerunning the helper is a no-op (idempotent) — already-migrated
   rows aren't re-encrypted (which would change the ciphertext) and
   rows without sensitive keys are skipped.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa


_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "2026_05_04_executor_config_api_key_encrypted.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("executor_api_key_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def in_memory_engine_with_legacy_row():
    """Fresh in-memory SQLite with the post-migration schema (column
    present) seeded with a legacy-shaped row whose plaintext key is
    still in ``config`` JSON."""
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE executor_configs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    executor_type TEXT NOT NULL,
                    config TEXT NOT NULL DEFAULT '{}',
                    api_key_encrypted TEXT NULL,
                    description TEXT NULL,
                    is_selected INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
    return engine


def test_data_migration_encrypts_legacy_plaintext(in_memory_engine_with_legacy_row):
    engine = in_memory_engine_with_legacy_row
    rid = str(uuid.uuid4())
    legacy_config = {
        "bsgateway_url": "https://gw.example.dev",
        "bsgateway_api_key": "real-secret-token-xyz",
        "model": "claude_code",
    }

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO executor_configs "
                "(id, tenant_id, name, executor_type, config) "
                "VALUES (:id, :tid, 'BSGW', 'bsgateway', :cfg)"
            ),
            {"id": rid, "tid": str(uuid.uuid4()), "cfg": json.dumps(legacy_config)},
        )

    migration = _load_migration()
    with engine.begin() as conn:
        migration._migrate_plaintext_rows(conn)

    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT api_key_encrypted, config FROM executor_configs WHERE id=:id"),
            {"id": rid},
        ).fetchone()

    assert row is not None
    assert row.api_key_encrypted, "encrypted column should be populated"
    # Ciphertext is base64; the literal plaintext must not appear.
    assert "real-secret-token-xyz" not in row.api_key_encrypted

    cleaned = json.loads(row.config)
    assert "bsgateway_api_key" not in cleaned
    assert "api_key" not in cleaned
    assert cleaned["bsgateway_url"] == "https://gw.example.dev"
    assert cleaned["model"] == "claude_code"

    # Round-trip — the encrypted value must decrypt to the original.
    from backend.src.config import settings as app_settings  # noqa: PLC0415
    from backend.src.core.encryption import EncryptionManager  # noqa: PLC0415

    decrypted = EncryptionManager(app_settings.encryption_key).decrypt_value(
        row.api_key_encrypted
    )
    assert decrypted == "real-secret-token-xyz"


def test_data_migration_idempotent(in_memory_engine_with_legacy_row):
    """Rerunning the migration helper after a successful first run does
    not re-encrypt rows (their config no longer carries the plaintext
    keys, so the helper has nothing to do)."""
    engine = in_memory_engine_with_legacy_row
    rid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO executor_configs "
                "(id, tenant_id, name, executor_type, config) "
                "VALUES (:id, :tid, 'X', 'bsgateway', :cfg)"
            ),
            {
                "id": rid,
                "tid": str(uuid.uuid4()),
                "cfg": json.dumps({"bsgateway_api_key": "first-pass"}),
            },
        )

    migration = _load_migration()
    with engine.begin() as conn:
        migration._migrate_plaintext_rows(conn)
    with engine.begin() as conn:
        first_encrypted = conn.execute(
            sa.text("SELECT api_key_encrypted FROM executor_configs WHERE id=:id"),
            {"id": rid},
        ).scalar_one()

    with engine.begin() as conn:
        migration._migrate_plaintext_rows(conn)
    with engine.begin() as conn:
        second_encrypted = conn.execute(
            sa.text("SELECT api_key_encrypted FROM executor_configs WHERE id=:id"),
            {"id": rid},
        ).scalar_one()

    assert first_encrypted == second_encrypted, "second pass must not re-encrypt"


def test_data_migration_skips_rows_without_sensitive_keys(in_memory_engine_with_legacy_row):
    engine = in_memory_engine_with_legacy_row
    rid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO executor_configs "
                "(id, tenant_id, name, executor_type, config) "
                "VALUES (:id, :tid, 'NoKey', 'generic_llm', :cfg)"
            ),
            {
                "id": rid,
                "tid": str(uuid.uuid4()),
                "cfg": json.dumps({"model": "gpt-4o"}),
            },
        )

    migration = _load_migration()
    with engine.begin() as conn:
        migration._migrate_plaintext_rows(conn)
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT api_key_encrypted, config FROM executor_configs WHERE id=:id"),
            {"id": rid},
        ).fetchone()

    assert row.api_key_encrypted is None
    assert json.loads(row.config) == {"model": "gpt-4o"}
