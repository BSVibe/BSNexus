"""Seed default tenant and initial agents for development."""

import asyncio
import os
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


async def seed() -> None:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus",
    )
    engine = create_async_engine(url)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    tid = "00000000-0000-0000-0000-000000000000"

    async with Session() as db:
        # 1. Default tenant
        result = await db.execute(text("SELECT id FROM tenants WHERE id = :id"), {"id": tid})
        if result.scalar_one_or_none() is None:
            await db.execute(
                text("""
                    INSERT INTO tenants (id, name, slug, mission, owner_user_id, plan, settings)
                    VALUES (:id, :name, :slug, :mission, :owner, :plan, '{}'::jsonb)
                """),
                {
                    "id": tid,
                    "name": "BSVibe",
                    "slug": "bsvibe",
                    "mission": "Build the leading AI-native software development ecosystem",
                    "owner": "test-user-id",
                    "plan": "pro",
                },
            )
            print("Created default tenant")
        else:
            print("Default tenant already exists")

        # 2. Seed agents
        result = await db.execute(text("SELECT count(*) FROM agents"))
        count = result.scalar_one()
        if count == 0:
            agents = [
                {
                    "name": "Architect",
                    "role": "architect",
                    "title": "Project Architect",
                    "job_description": "Designs project architecture and decomposes into tasks through conversation",
                    "executor_type": "claude_api",
                    "capabilities": '["coding", "analysis"]',
                    "skills": "[]",
                    "monthly_budget_cents": 6000,
                },
                {
                    "name": "Developer",
                    "role": "engineer",
                    "title": "Senior Engineer",
                    "job_description": "Implements features, fixes bugs, writes tests",
                    "executor_type": "claude_code",
                    "capabilities": '["coding"]',
                    "skills": '["git-ops", "code-review"]',
                    "monthly_budget_cents": 30000,
                },
                {
                    "name": "Reviewer",
                    "role": "reviewer",
                    "title": "QA Reviewer",
                    "job_description": "Reviews code quality, runs tests, ensures standards",
                    "executor_type": "claude_api",
                    "capabilities": '["coding", "analysis"]',
                    "skills": '["code-review"]',
                    "monthly_budget_cents": 10000,
                },
            ]
            for a in agents:
                await db.execute(
                    text("""
                        INSERT INTO agents (
                            id, tenant_id, name, role, title, job_description,
                            executor_type, executor_config, skills, capabilities,
                            heartbeat_enabled, monthly_budget_cents,
                            current_month_spent_cents, status, is_active
                        ) VALUES (
                            :id, :tid, :name, :role, :title, :job_desc,
                            :exec_type, CAST('{}' AS jsonb), CAST(:skills AS jsonb), CAST(:caps AS jsonb),
                            false, :budget, 0, 'online', true
                        )
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "tid": tid,
                        "name": a["name"],
                        "role": a["role"],
                        "title": a["title"],
                        "job_desc": a["job_description"],
                        "exec_type": a["executor_type"],
                        "skills": a["skills"],
                        "caps": a["capabilities"],
                        "budget": a["monthly_budget_cents"],
                    },
                )
            print(f"Created {len(agents)} seed agents")
        else:
            print(f"{count} agents already exist")

        # 3. Seed mission goal
        result = await db.execute(text("SELECT count(*) FROM goals WHERE level = 'mission'"))
        if result.scalar_one() == 0:
            await db.execute(
                text("""
                    INSERT INTO goals (id, tenant_id, level, title, description)
                    VALUES (:id, :tid, 'mission', :title, :desc)
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tid": tid,
                    "title": "Build the leading AI-native development ecosystem",
                    "desc": "Enable 1-person companies to operate at enterprise scale with AI agents",
                },
            )
            print("Created mission goal")
        else:
            print("Mission goal already exists")

        await db.commit()

    await engine.dispose()
    print("Seed complete")


if __name__ == "__main__":
    asyncio.run(seed())
