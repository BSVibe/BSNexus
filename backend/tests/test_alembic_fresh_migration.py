"""Fresh-PG alembic smoke test.

Runs ``alembic upgrade head`` against an empty PostgreSQL database.
Catches issues like:

- Enum double-creation (CREATE TYPE conflicts).
- FK references to tables that haven't been created yet.
- ``ALTER COLUMN`` targets that don't exist after prior drops.

Skipped unless ``BSNEXUS_INTEGRATION_PG_URL`` is set, so local quick
runs stay fast. CI sets it against a throwaway container per skill
``alembic-fresh-pg-smoke-test``.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_PG_URL = os.environ.get("BSNEXUS_INTEGRATION_PG_URL")

pytestmark = pytest.mark.skipif(
    not _PG_URL,
    reason="Set BSNEXUS_INTEGRATION_PG_URL to a throwaway PG to enable.",
)


@pytest.mark.asyncio
async def test_alembic_upgrade_head_on_fresh_pg():
    import os
    import subprocess

    engine = create_async_engine(_PG_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    # Run alembic out-of-process — env.py uses asyncio.run, which clashes
    # with the pytest-asyncio loop when invoked in-process.
    result = subprocess.run(
        ["uv", "run", "--project", ".", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": _PG_URL},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"

    engine = create_async_engine(_PG_URL)
    async with engine.connect() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' ORDER BY table_name"
                    )
                )
            )
            .scalars()
            .all()
        )
    await engine.dispose()

    expected = {
        "brief_snapshots",
        "decisions",
        "deliverables",
        "directions",
        "proof_attempts",
        "proof_policies",
        "projects",
        "requests",
        "run_attempts",
        "tool_events",
        "work_plans",
        "work_steps",
    }
    missing = expected - set(tables)
    assert not missing, f"Missing tables after upgrade: {missing}"


@pytest.mark.asyncio
async def test_fresh_pg_schema_matches_model_metadata():
    """Every column on every SQLAlchemy model must exist in the live
    schema after ``alembic upgrade head``.

    This is the guard the empty-G8.3-migration incident (revision
    ``cb713805c963`` shipped as the bare ``alembic revision`` scaffold)
    proved necessary: ``alembic upgrade head`` "succeeded" doing
    nothing, so ``requests.pr_number`` / ``pr_url`` were declared on the
    model but absent from the DB, and the first Direction POST 500'd.
    A table-name-only check can't catch a missing *column*; reflecting
    the live schema and diffing it against ``Base.metadata`` can.
    """
    import os
    import subprocess

    from sqlalchemy import inspect as sa_inspect

    from backend.src.models import Base

    engine = create_async_engine(_PG_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()

    result = subprocess.run(
        ["uv", "run", "--project", ".", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": _PG_URL},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"

    engine = create_async_engine(_PG_URL)
    async with engine.connect() as conn:
        live_columns = await conn.run_sync(
            lambda sync_conn: {
                table_name: {col["name"] for col in sa_inspect(sync_conn).get_columns(table_name)}
                for table_name in sa_inspect(sync_conn).get_table_names()
            }
        )
    await engine.dispose()

    drift: list[str] = []
    for table in Base.metadata.sorted_tables:
        live = live_columns.get(table.name)
        if live is None:
            drift.append(f"table '{table.name}' missing from live schema")
            continue
        for column in table.columns:
            if column.name not in live:
                drift.append(f"{table.name}.{column.name} declared on model but missing from live schema")
    assert not drift, "Model/schema drift after upgrade head:\n" + "\n".join(drift)
