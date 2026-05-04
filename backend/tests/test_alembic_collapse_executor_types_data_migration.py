"""Data-migration coverage for ``2026_05_04_collapse_executor_types``.

The fresh-PG smoke test only proves the migration applies cleanly to
an empty DB. This pins the actual data lift: legacy ``executor_type``
values (``claude_code`` / ``codex`` / ``opencode`` / ``worker``) must
become ``bsgateway`` with the original value preserved in
``config.model``.
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
    / "2026_05_04_collapse_executor_types.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "collapse_executor_types_migration", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def in_memory_engine():
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


def _seed(engine, *, executor_type: str, config: dict) -> str:
    rid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO executor_configs (id, tenant_id, name, executor_type, config) "
                "VALUES (:id, :tid, :name, :t, :cfg)"
            ),
            {
                "id": rid,
                "tid": str(uuid.uuid4()),
                "name": f"row-{executor_type}",
                "t": executor_type,
                "cfg": json.dumps(config),
            },
        )
    return rid


def _read(engine, rid: str) -> tuple[str, dict]:
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT executor_type, config FROM executor_configs WHERE id=:id"),
            {"id": rid},
        ).fetchone()
    return row.executor_type, json.loads(row.config)


def test_lift_claude_code_to_bsgateway_with_model(in_memory_engine):
    rid = _seed(
        in_memory_engine,
        executor_type="claude_code",
        config={"bsgateway_url": "http://gw"},
    )

    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)

    typ, cfg = _read(in_memory_engine, rid)
    assert typ == "bsgateway"
    assert cfg["model"] == "claude_code"
    assert cfg["bsgateway_url"] == "http://gw", "non-sensitive config keys preserved"


@pytest.mark.parametrize("legacy_type", ["claude_code", "codex", "opencode"])
def test_lift_each_cli_type_into_model(in_memory_engine, legacy_type):
    rid = _seed(in_memory_engine, executor_type=legacy_type, config={})
    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)
    typ, cfg = _read(in_memory_engine, rid)
    assert typ == "bsgateway"
    assert cfg["model"] == legacy_type


def test_worker_renamed_to_bsgateway_without_model_lift(in_memory_engine):
    """``worker`` was a BSGateway alias — just rename, don't synthesise
    a ``config.model`` entry."""
    rid = _seed(
        in_memory_engine,
        executor_type="worker",
        config={"bsgateway_url": "http://gw", "model": "openai/gpt-4o"},
    )
    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)
    typ, cfg = _read(in_memory_engine, rid)
    assert typ == "bsgateway"
    assert cfg["model"] == "openai/gpt-4o", "operator's model override preserved"


def test_existing_bsgateway_row_left_alone(in_memory_engine):
    rid = _seed(
        in_memory_engine,
        executor_type="bsgateway",
        config={"model": "anthropic/claude-3-5-sonnet"},
    )
    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)
    typ, cfg = _read(in_memory_engine, rid)
    assert typ == "bsgateway"
    assert cfg["model"] == "anthropic/claude-3-5-sonnet"


def test_existing_generic_llm_row_left_alone(in_memory_engine):
    rid = _seed(
        in_memory_engine,
        executor_type="generic_llm",
        config={"model": "ollama/qwen3-coder"},
    )
    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)
    typ, cfg = _read(in_memory_engine, rid)
    assert typ == "generic_llm"
    assert cfg["model"] == "ollama/qwen3-coder"


def test_lift_preserves_explicit_model_override(in_memory_engine):
    """If a legacy CLI-typed row already had ``config.model`` set, the
    operator override wins — don't overwrite with ``executor_type``."""
    rid = _seed(
        in_memory_engine,
        executor_type="claude_code",
        config={"model": "anthropic/claude-3-7-sonnet"},
    )
    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)
    typ, cfg = _read(in_memory_engine, rid)
    assert typ == "bsgateway"
    assert cfg["model"] == "anthropic/claude-3-7-sonnet", "operator override preserved"


def test_idempotent(in_memory_engine):
    """Running the migration twice in a row produces the same final
    state — important for restart-on-failure scenarios."""
    rid = _seed(in_memory_engine, executor_type="codex", config={})
    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)
    with in_memory_engine.begin() as conn:
        _load_migration()._collapse_legacy_rows(conn)
    typ, cfg = _read(in_memory_engine, rid)
    assert typ == "bsgateway"
    assert cfg["model"] == "codex"


def test_normalise_config_handles_string_and_dict():
    """Helper accepts both shapes — PG asyncpg returns str, SQLite returns dict."""
    mod = _load_migration()
    assert mod._normalise_config({"a": 1}) == {"a": 1}
    assert mod._normalise_config('{"a": 1}') == {"a": 1}
    assert mod._normalise_config("not json") == {}
    assert mod._normalise_config(None) == {}
    assert mod._normalise_config(123) == {}
