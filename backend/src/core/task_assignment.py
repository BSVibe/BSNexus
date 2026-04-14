"""Task-to-agent assignment — keyword-based capability matching.

Maps task text (title + description) to the best agent based on
keyword → capability → agent.capabilities overlap.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.src.models import Agent

# Keyword → required capabilities mapping.
# Order matters: first match wins for tie-breaking.
KEYWORD_CAPABILITIES: list[tuple[list[str], list[str]]] = [
    # Design keywords → design capability
    (["design", "ui", "ux", "디자인", "화면", "프로토타입", "mockup", "wireframe", "screen", ".bsd"],
     ["design"]),
    # Architecture keywords → architect capability
    (["architect", "아키텍처", "tech stack", "기술 스택", "infrastructure", "system design"],
     ["architect"]),
    # Backend/coding keywords → coding capability
    (["backend", "api", "database", "서버", "백엔드", "코딩", "implement", "develop", "code"],
     ["coding"]),
    # Frontend keywords → coding + design
    (["frontend", "프론트엔드", "component", "react", "css"],
     ["coding", "design"]),
    # Marketing keywords → marketing capability
    (["marketing", "마케팅", "content", "콘텐츠", "landing", "seo", "광고", "브랜드"],
     ["marketing"]),
    # Analysis/research → analyze capability
    (["research", "analysis", "조사", "분석", "report", "보고서"],
     ["analyze"]),
    # Planning → plan capability
    (["plan", "strategy", "roadmap", "기획", "전략", "로드맵"],
     ["plan"]),
]


def match_agent_for_task(
    text: str,
    agents: list["Agent"],
    *,
    exclude_id: uuid.UUID | None = None,
) -> "Agent | None":
    """Find the best agent for a task based on keyword matching.

    Args:
        text: Lowercase task title + description.
        agents: All active agents to consider.
        exclude_id: Agent to exclude (typically the creator).

    Returns:
        Best matching agent, or None if no match.
    """
    candidates = [a for a in agents if a.id != exclude_id and a.is_active]
    if not candidates:
        return None

    # Find required capabilities from keywords
    required_caps: set[str] = set()
    for keywords, caps in KEYWORD_CAPABILITIES:
        if any(kw in text for kw in keywords):
            required_caps.update(caps)
            break  # First match wins

    if not required_caps:
        return None  # No keyword match → don't auto-assign

    # Score each agent by capability overlap
    best_agent = None
    best_score = 0
    for agent in candidates:
        agent_caps = {(c or "").strip().lower() for c in (agent.capabilities or [])}
        overlap = len(required_caps & agent_caps)
        if overlap > best_score:
            best_score = overlap
            best_agent = agent

    return best_agent
