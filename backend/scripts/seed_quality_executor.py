"""Seed an ExecutorConfig row pointing at local Ollama qwen3-coder:30b
for M0 live-runner. Idempotent: upserts on tenant_id."""

from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.src.config import settings
from backend.src.models.executor_config import ExecutorConfig, ExecutorKind
from backend.src.core.encryption import EncryptionManager


async def main(tenant_id: str) -> None:
    tid = uuid.UUID(tenant_id)
    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    enc = EncryptionManager(settings.encryption_key)
    async with Session() as s:
        row = (
            await s.execute(select(ExecutorConfig).where(ExecutorConfig.tenant_id == tid))
        ).scalar_one_or_none()
        if row is None:
            row = ExecutorConfig(tenant_id=tid)
            s.add(row)
        row.kind = ExecutorKind.llm_api
        row.base_url = "http://host.docker.internal:11434"
        row.model = "ollama_chat/qwen3-coder:30b"
        row.api_key_encrypted = enc.encrypt_value("ollama-no-auth")
        row.extra_config = {}
        await s.commit()
        print(f"seeded executor_config tenant={tid} model={row.model}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
