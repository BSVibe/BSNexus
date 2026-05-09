from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend.src.core.domain import BriefScope


def empty_brief_snapshot(project_id: uuid.UUID | None = None) -> dict:
    return {
        "scope": BriefScope.project.value if project_id is not None else BriefScope.company.value,
        "project_id": project_id,
        "sections": {
            "shipped": [],
            "needs_decision": [],
            "blocked": [],
            "running": [],
            "next": [],
        },
        "generated_at": datetime.now(timezone.utc),
    }
