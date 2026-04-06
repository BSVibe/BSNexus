"""Agent Templates API — predefined org chart templates for quick setup."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.tenant_context import DEFAULT_TENANT_ID
from backend.src.models import Agent
from backend.src.repositories.agent_repository import AgentRepository
from backend.src.schemas.agent import AgentResponse
from backend.src.storage.database import get_db

router = APIRouter(prefix="/api/v1/agent-templates", tags=["agent-templates"])


class AgentTemplate(BaseModel):
    name: str
    role: str
    title: str
    job_description: str
    executor_type: str
    capabilities: list[str]
    children: list["AgentTemplate"] = []


class OrgTemplate(BaseModel):
    id: str
    name: str
    description: str
    agent_count: int
    agents: list[AgentTemplate]


TEMPLATES: dict[str, OrgTemplate] = {
    "startup": OrgTemplate(
        id="startup",
        name="Startup Team",
        description="Small team for early-stage startups. CEO leads with a CTO, PM, and Marketer.",
        agent_count=7,
        agents=[
            AgentTemplate(
                name="CEO",
                role="ceo",
                title="Chief Executive Officer",
                job_description="Sets company vision, makes strategic decisions, coordinates all departments",
                executor_type="claude_api",
                capabilities=["analysis", "writing", "general"],
                children=[
                    AgentTemplate(
                        name="CTO",
                        role="cto",
                        title="Chief Technology Officer",
                        job_description="Leads technical architecture and engineering team",
                        executor_type="claude_api",
                        capabilities=["coding", "analysis"],
                        children=[
                            AgentTemplate(
                                name="Engineer",
                                role="engineer",
                                title="Full-Stack Engineer",
                                job_description="Implements features, fixes bugs, writes tests, deploys code",
                                executor_type="claude_code",
                                capabilities=["coding"],
                            ),
                            AgentTemplate(
                                name="QA",
                                role="qa_engineer",
                                title="QA Engineer",
                                job_description="Reviews code quality, runs tests, validates requirements, reports bugs",
                                executor_type="claude_api",
                                capabilities=["coding", "analysis"],
                            ),
                        ],
                    ),
                    AgentTemplate(
                        name="PM",
                        role="product_manager",
                        title="Product Manager",
                        job_description="Defines product requirements, prioritizes backlog, manages roadmap",
                        executor_type="generic_llm",
                        capabilities=["analysis", "writing", "general"],
                    ),
                    AgentTemplate(
                        name="Marketer",
                        role="marketer",
                        title="Growth Marketer",
                        job_description="Creates marketing content, manages campaigns, analyzes metrics",
                        executor_type="generic_llm",
                        capabilities=["marketing", "writing", "research"],
                    ),
                    AgentTemplate(
                        name="Designer",
                        role="designer",
                        title="Product Designer",
                        job_description="Designs user interfaces, creates prototypes, maintains design system",
                        executor_type="generic_llm",
                        capabilities=["writing", "analysis"],
                    ),
                ],
            ),
        ],
    ),
    "minimal": OrgTemplate(
        id="minimal",
        name="Minimal",
        description="Bare minimum to get started. CEO and one Developer.",
        agent_count=2,
        agents=[
            AgentTemplate(
                name="Lead",
                role="lead",
                title="Team Lead",
                job_description="Leads the team, makes decisions, reviews work",
                executor_type="claude_api",
                capabilities=["coding", "analysis", "general"],
                children=[
                    AgentTemplate(
                        name="Developer",
                        role="engineer",
                        title="Developer",
                        job_description="Implements features and fixes bugs",
                        executor_type="claude_code",
                        capabilities=["coding"],
                    ),
                ],
            ),
        ],
    ),
    "enterprise": OrgTemplate(
        id="enterprise",
        name="Enterprise",
        description="Full company structure with C-level executives and department teams.",
        agent_count=11,
        agents=[
            AgentTemplate(
                name="CEO",
                role="ceo",
                title="Chief Executive Officer",
                job_description="Sets company vision and strategy",
                executor_type="claude_api",
                capabilities=["analysis", "writing", "general"],
                children=[
                    AgentTemplate(
                        name="CTO",
                        role="cto",
                        title="Chief Technology Officer",
                        job_description="Technical architecture and engineering leadership",
                        executor_type="claude_api",
                        capabilities=["coding", "analysis"],
                        children=[
                            AgentTemplate(
                                name="Backend Engineer",
                                role="backend_engineer",
                                title="Senior Backend Engineer",
                                job_description="Backend API development, database design, infrastructure",
                                executor_type="claude_code",
                                capabilities=["coding"],
                            ),
                            AgentTemplate(
                                name="Frontend Engineer",
                                role="frontend_engineer",
                                title="Senior Frontend Engineer",
                                job_description="Frontend UI development, component design, performance",
                                executor_type="claude_code",
                                capabilities=["coding"],
                            ),
                            AgentTemplate(
                                name="DevOps",
                                role="devops",
                                title="DevOps Engineer",
                                job_description="CI/CD pipelines, infrastructure, monitoring, deployment",
                                executor_type="claude_code",
                                capabilities=["coding"],
                            ),
                            AgentTemplate(
                                name="QA Lead",
                                role="qa_lead",
                                title="QA Lead",
                                job_description="Test strategy, code review, quality standards, bug triage",
                                executor_type="claude_api",
                                capabilities=["coding", "analysis"],
                            ),
                        ],
                    ),
                    AgentTemplate(
                        name="CPO",
                        role="cpo",
                        title="Chief Product Officer",
                        job_description="Product strategy, roadmap, user research",
                        executor_type="generic_llm",
                        capabilities=["analysis", "writing", "research"],
                        children=[
                            AgentTemplate(
                                name="Product Manager",
                                role="product_manager",
                                title="Product Manager",
                                job_description="Feature specs, backlog grooming, stakeholder communication",
                                executor_type="generic_llm",
                                capabilities=["analysis", "writing"],
                            ),
                            AgentTemplate(
                                name="Designer",
                                role="designer",
                                title="Product Designer",
                                job_description="UI/UX design, prototyping, design system",
                                executor_type="generic_llm",
                                capabilities=["writing", "analysis"],
                            ),
                        ],
                    ),
                    AgentTemplate(
                        name="CMO",
                        role="cmo",
                        title="Chief Marketing Officer",
                        job_description="Marketing strategy, brand, content, growth",
                        executor_type="generic_llm",
                        capabilities=["marketing", "writing", "research"],
                        children=[
                            AgentTemplate(
                                name="Content Writer",
                                role="content_writer",
                                title="Content Writer",
                                job_description="Blog posts, documentation, copywriting",
                                executor_type="generic_llm",
                                capabilities=["writing", "marketing"],
                            ),
                        ],
                    ),
                ],
            ),
        ],
    ),
}


@router.get("", response_model=list[OrgTemplate])
async def list_templates() -> list[OrgTemplate]:
    """List available org chart templates."""
    return list(TEMPLATES.values())


@router.get("/{template_id}", response_model=OrgTemplate)
async def get_template(template_id: str) -> OrgTemplate:
    template = TEMPLATES.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return template


@router.post("/{template_id}/apply", response_model=list[AgentResponse])
async def apply_template(template_id: str, db: AsyncSession = Depends(get_db)) -> list[AgentResponse]:
    """Apply a template — creates agents with hierarchy. Existing agents are NOT deleted."""
    template = TEMPLATES.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    repo = AgentRepository(db)
    created: list[Agent] = []

    async def create_tree(templates: list[AgentTemplate], parent_id: uuid.UUID | None) -> None:
        for t in templates:
            agent = Agent(
                tenant_id=DEFAULT_TENANT_ID,
                name=t.name,
                role=t.role,
                title=t.title,
                job_description=t.job_description,
                executor_type=t.executor_type,
                capabilities=t.capabilities,
                parent_agent_id=parent_id,
            )
            await repo.add(agent)
            await db.flush()
            await db.refresh(agent)
            created.append(agent)
            if t.children:
                await create_tree(t.children, agent.id)

    await create_tree(template.agents, None)
    await repo.commit()
    return [AgentResponse.model_validate(a) for a in created]
