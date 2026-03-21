"""Architect Service — core business logic for design sessions.

Both the CLI and API use this service layer.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from typing import Any

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.src import models
from backend.src.core.llm_client import LLMClient, LLMConfig
from backend.src.prompts.loader import get_prompt
from backend.src.repositories.design_session_repository import DesignSessionRepository
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.project_repository import ProjectRepository
from backend.src.repositories.task_repository import TaskRepository

# Type alias for session factory (async_sessionmaker or compatible callable)
SessionFactory = async_sessionmaker[AsyncSession] | Callable[[], AsyncSession]

FINALIZE_MARKER = "[FINALIZE]"
_CONTEXT_RE = re.compile(r"<design_context>(.*?)</design_context>", re.DOTALL)
_CREATE_TASK_RE = re.compile(r"\[CREATE_TASK\](.*?)\[/CREATE_TASK\]", re.DOTALL)
_MODIFY_TASK_RE = re.compile(r"\[MODIFY_TASK\](.*?)\[/MODIFY_TASK\]", re.DOTALL)


def slugify(value: str) -> str:
    """Convert a string to a URL-friendly slug."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"[-\s]+", "-", value).strip("-")


def clean_response(text: str) -> tuple[str, bool, str | None]:
    """Strip [FINALIZE] marker and extract design_context from text."""
    has_finalize = FINALIZE_MARKER in text
    design_context: str | None = None
    ctx_match = _CONTEXT_RE.search(text)
    if ctx_match:
        design_context = ctx_match.group(1).strip()
    cleaned = text.replace(FINALIZE_MARKER, "")
    cleaned = _CONTEXT_RE.sub("", cleaned).strip()
    return cleaned, has_finalize, design_context


def strip_action_markers(text: str) -> str:
    """Remove action marker blocks from user-visible text."""
    text = _CREATE_TASK_RE.sub("", text)
    text = _MODIFY_TASK_RE.sub("", text)
    return text.strip()


def build_llm_config(llm_config_dict: dict[str, Any] | None) -> LLMConfig:
    """Build an LLMConfig from a dict stored in session.llm_config."""
    if not llm_config_dict or "api_key" not in llm_config_dict:
        raise ValueError("LLM configuration with api_key is required")
    kwargs: dict[str, Any] = {"api_key": llm_config_dict["api_key"]}
    model = llm_config_dict.get("model")
    if model is not None and model != "":
        kwargs["model"] = model
    base_url = llm_config_dict.get("base_url")
    if base_url is not None and base_url != "":
        kwargs["base_url"] = base_url
    return LLMConfig(**kwargs)


def extract_design_context(session: models.DesignSession) -> str | None:
    """Extract design_context from the last assistant chat message."""
    chat_messages = [
        m
        for m in session.messages
        if m.message_type == models.MessageType.chat and m.role == models.MessageRole.assistant
    ]
    if not chat_messages:
        return None
    last = sorted(chat_messages, key=lambda m: m.created_at)[-1]
    match = _CONTEXT_RE.search(last.content)
    return match.group(1).strip() if match else None


def build_message_history(session: models.DesignSession, project: models.Project | None = None) -> list[dict[str, str]]:
    """Build LLM message history from a session's messages."""
    if session.status == models.DesignSessionStatus.project_bound and project:
        project_context = build_project_context(project)
        system_template = get_prompt("architect", "system_project_bound")
        system_content = system_template.format(project_context=project_context)
    else:
        system_content = get_prompt("architect", "system")

    history: list[dict[str, str]] = [{"role": "system", "content": system_content}]
    chat_messages = [m for m in session.messages if m.message_type == models.MessageType.chat]
    sorted_messages = sorted(chat_messages, key=lambda m: m.created_at)
    history.extend(
        {
            "role": m.role.value if isinstance(m.role, models.MessageRole) else str(m.role),
            "content": m.content,
        }
        for m in sorted_messages
    )
    return history


def build_project_context(project: models.Project) -> str:
    """Build a text summary of current project state."""
    lines = [
        f"Project: {project.name}",
        f"Status: {project.status.value}",
        f"Description: {project.description}",
        "",
        "Phases:",
    ]
    for phase in sorted(project.phases, key=lambda p: p.order):
        lines.append(f"  [{phase.status.value}] {phase.name}")
        for task in sorted(phase.tasks, key=lambda t: t.created_at):
            task_type_label = f" ({task.task_type.value})" if task.task_type != models.TaskType.feature else ""
            lines.append(f"    - [{task.status.value}] {task.title}{task_type_label} (id: {task.id})")
    return "\n".join(lines)


class ArchitectService:
    """Architect core business logic — used by both CLI and API."""

    def __init__(self, db: AsyncSession, session_factory: SessionFactory | None = None) -> None:
        self.db = db
        self._session_factory = session_factory

    async def create_session(self, llm_config_dict: dict[str, Any], name: str | None = None) -> models.DesignSession:
        """Create a new design session."""
        repo = DesignSessionRepository(self.db)
        session = await repo.add(models.DesignSession(name=name, llm_config=llm_config_dict))
        await repo.commit()
        loaded = await repo.get_by_id(session.id)
        if loaded is None:
            raise RuntimeError("Failed to create session")
        return loaded

    async def send_message(
        self, session_id: uuid.UUID, content: str
    ) -> tuple[models.DesignMessage, str, bool, str | None]:
        """Send a message and get non-streaming LLM response.

        Returns (assistant_message, cleaned_text, has_finalize, design_context).
        """
        repo = DesignSessionRepository(self.db)
        session = await repo.get_by_id(session_id)
        if session is None:
            raise ValueError("Session not found")
        if session.status == models.DesignSessionStatus.cancelled:
            raise ValueError("Session is cancelled")

        project: models.Project | None = None
        if session.status == models.DesignSessionStatus.project_bound and session.project_id:
            project = await self.load_project_with_tasks(session.project_id)

        await repo.add_message(session.id, models.MessageRole.user, content)

        messages = build_message_history(session, project=project)
        messages.append({"role": "user", "content": content})

        config = build_llm_config(session.llm_config)
        client = LLMClient(config)
        response_text = await client.chat(messages)

        cleaned_text, has_finalize, design_context = clean_response(response_text)

        if session.status == models.DesignSessionStatus.project_bound:
            await self.execute_action_markers(response_text, session)
            cleaned_text = strip_action_markers(cleaned_text)

        assistant_msg = await repo.add_message(
            session.id,
            models.MessageRole.assistant,
            response_text.replace(FINALIZE_MARKER, "").strip(),
        )
        await repo.commit()
        await repo.refresh(assistant_msg)

        return assistant_msg, cleaned_text, has_finalize, design_context

    async def finalize(
        self,
        session_id: uuid.UUID,
        repo_path: str,
        pm_llm_config: dict[str, Any] | None = None,
    ) -> models.Project:
        """Finalize a design session into a project with phases and tasks.

        Uses a separate DB session for the write phase to avoid holding a
        connection idle during the long-running LLM call.  The session factory
        is provided via DI (constructor) rather than imported directly.
        """
        if self._session_factory is None:
            raise RuntimeError("session_factory is required for finalize()")

        session_repo = DesignSessionRepository(self.db)
        session = await session_repo.get_by_id(session_id)
        if session is None:
            raise ValueError("Session not found")
        if session.status == models.DesignSessionStatus.project_bound:
            if session.project_id:
                project_repo = ProjectRepository(self.db)
                existing = await project_repo.get_by_id(session.project_id)
                if existing:
                    return existing
            raise ValueError("Session is finalized but project not found")
        if session.status != models.DesignSessionStatus.active:
            raise ValueError("Session is not active")

        design_context = extract_design_context(session)
        llm_config_dict = session.llm_config

        finalize_template = get_prompt("architect", "finalize")
        if design_context:
            finalize_prompt = finalize_template.format(design_context=design_context)
            messages = [
                {"role": "system", "content": get_prompt("architect", "system")},
                {"role": "user", "content": finalize_prompt},
            ]
        else:
            finalize_prompt = finalize_template.format(design_context="(see conversation history above)")
            messages = build_message_history(session)
            messages.append({"role": "user", "content": finalize_prompt})

        config = build_llm_config(llm_config_dict)
        client = LLMClient(config)
        result = await client.structured_output(messages=messages, response_format={"type": "json_object"})

        # Write results with a fresh DB session (via injected factory)
        async with self._session_factory() as write_db:
            write_session_repo = DesignSessionRepository(write_db)

            session = await write_session_repo.get_by_id(session_id, load_messages=False)
            if session is None:
                raise ValueError("Session not found")
            if session.status == models.DesignSessionStatus.project_bound:
                if session.project_id:
                    project_repo = ProjectRepository(write_db)
                    existing = await project_repo.get_by_id(session.project_id)
                    if existing:
                        return existing
                raise ValueError("Session is finalized but project not found")

            await write_session_repo.add_message(
                session.id,
                models.MessageRole.user,
                finalize_prompt,
                message_type=models.MessageType.internal,
            )
            await write_session_repo.add_message(
                session.id,
                models.MessageRole.assistant,
                json.dumps(result, ensure_ascii=False),
                message_type=models.MessageType.internal,
            )

            project_llm_config: dict[str, Any] = {"architect": llm_config_dict}
            if pm_llm_config:
                project_llm_config["pm"] = pm_llm_config

            project = models.Project(
                name=result.get("project_name", "Untitled Project"),
                description=result.get("project_description", ""),
                repo_path=repo_path,
                status=models.ProjectStatus.design,
                llm_config=project_llm_config,
            )
            write_db.add(project)
            await write_db.flush()

            all_tasks: list[models.Task] = []
            phases_data = result.get("phases", [])
            phases_by_order: dict[int, models.Phase] = {}
            phase_task_indices: dict[int, set[int]] = {}
            flat_idx_counter = 0

            for phase_order, phase_data in enumerate(phases_data, start=1):
                phase_name = phase_data.get("name", f"Phase {phase_order}")
                branch_name = f"phase/{slugify(phase_name)}"

                phase = models.Phase(
                    project_id=project.id,
                    name=phase_name,
                    description=phase_data.get("description"),
                    branch_name=branch_name,
                    order=phase_order,
                )
                write_db.add(phase)
                await write_db.flush()
                phases_by_order[phase_order] = phase

                task_indices: set[int] = set()
                for task_data in phase_data.get("tasks", []):
                    priority_str = task_data.get("priority", "medium")
                    try:
                        priority = models.TaskPriority(priority_str)
                    except ValueError:
                        priority = models.TaskPriority.medium

                    task = models.Task(
                        project_id=project.id,
                        phase_id=phase.id,
                        title=task_data.get("title", "Untitled Task"),
                        description=task_data.get("description"),
                        priority=priority,
                        worker_prompt={"prompt": task_data.get("worker_prompt", "")},
                        qa_prompt={"prompt": task_data.get("qa_prompt", "")},
                        branch_name=branch_name,
                    )
                    write_db.add(task)
                    await write_db.flush()
                    all_tasks.append(task)
                    task_indices.add(flat_idx_counter)
                    flat_idx_counter += 1

                phase_task_indices[phase_order] = task_indices

            # Wire up dependencies
            task_repo = TaskRepository(write_db)
            tasks_with_deps: set[int] = set()
            flat_index = 0
            for phase_data in phases_data:
                for task_data in phase_data.get("tasks", []):
                    depends_on_indices = task_data.get("depends_on_indices", [])
                    if depends_on_indices:
                        dep_ids = []
                        for idx in depends_on_indices:
                            if 0 <= idx < len(all_tasks):
                                dep_ids.append(all_tasks[idx].id)
                        if dep_ids:
                            await task_repo.add_dependencies(all_tasks[flat_index].id, dep_ids)
                            all_tasks[flat_index].status = models.TaskStatus.waiting
                            tasks_with_deps.add(flat_index)
                    flat_index += 1

            if not phases_by_order:
                raise ValueError("Design must contain at least one phase with tasks")

            first_order = min(phases_by_order.keys())
            phases_by_order[first_order].status = models.PhaseStatus.active
            first_phase_indices = phase_task_indices.get(first_order, set())
            for i, task in enumerate(all_tasks):
                if i not in tasks_with_deps and i in first_phase_indices:
                    task.status = models.TaskStatus.ready

            session.status = models.DesignSessionStatus.project_bound
            session.project_id = project.id
            await write_db.commit()

            project_repo = ProjectRepository(write_db)
            loaded_project = await project_repo.get_by_id(project.id)
            if loaded_project is None:
                raise RuntimeError("Failed to load project after creation")
            return loaded_project

    async def list_sessions(self, status: models.DesignSessionStatus | None = None) -> list[models.DesignSession]:
        """List design sessions."""
        repo = DesignSessionRepository(self.db)
        sessions = await repo.list_sessions(status=status)
        for s in sessions:
            s.messages = [m for m in s.messages if m.message_type == models.MessageType.chat]
        return sessions

    async def get_session(self, session_id: uuid.UUID) -> models.DesignSession | None:
        """Get a session by ID."""
        repo = DesignSessionRepository(self.db)
        session = await repo.get_by_id(session_id)
        if session:
            session.messages = [m for m in session.messages if m.message_type == models.MessageType.chat]
        return session

    # -- Private helpers --

    async def load_project_with_tasks(self, project_id: uuid.UUID) -> models.Project | None:
        """Load a project with phases and tasks eagerly loaded."""
        result = await self.db.execute(
            select(models.Project)
            .where(models.Project.id == project_id)
            .options(selectinload(models.Project.phases).selectinload(models.Phase.tasks))
        )
        return result.scalar_one_or_none()

    async def execute_action_markers(self, text: str, session: models.DesignSession) -> list[dict[str, Any]]:
        """Parse and execute action markers from LLM response."""
        actions: list[dict[str, Any]] = []
        if not session.project_id:
            return actions

        task_repo = TaskRepository(self.db)

        phase_repo = PhaseRepository(self.db)
        phases = await phase_repo.list_by_project(session.project_id)
        active_phase = next((p for p in phases if p.status == models.PhaseStatus.active), None)

        for match in _CREATE_TASK_RE.finditer(text):
            if not active_phase:
                break

            try:
                task_data = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue

            priority_str = task_data.get("priority", "medium")
            try:
                priority = models.TaskPriority(priority_str)
            except ValueError:
                priority = models.TaskPriority.medium

            task_type_str = task_data.get("task_type", "feature")
            try:
                task_type = models.TaskType(task_type_str)
            except ValueError:
                task_type = models.TaskType.feature

            new_task = models.Task(
                project_id=session.project_id,
                phase_id=active_phase.id,
                title=task_data.get("title", "Untitled Task"),
                description=task_data.get("description"),
                priority=priority,
                task_type=task_type,
                source=models.TaskSource.architect,
                status=models.TaskStatus.ready,
                worker_prompt={"prompt": task_data.get("worker_prompt", "")},
                qa_prompt={"prompt": task_data.get("qa_prompt", "")},
                branch_name=active_phase.branch_name,
            )
            self.db.add(new_task)
            await self.db.flush()
            actions.append({"type": "task_created", "task_id": str(new_task.id), "title": new_task.title})

        for match in _MODIFY_TASK_RE.finditer(text):
            try:
                task_data = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue

            task_id_str = task_data.get("task_id")
            if not task_id_str:
                continue
            try:
                task_id = uuid.UUID(task_id_str)
            except ValueError:
                continue

            task = await task_repo.get_by_id(task_id)
            if not task or task.project_id != session.project_id:
                continue
            if task.status not in (models.TaskStatus.waiting, models.TaskStatus.ready):
                continue

            if "title" in task_data:
                task.title = task_data["title"]
            if "description" in task_data:
                task.description = task_data["description"]
            if "priority" in task_data:
                try:
                    task.priority = models.TaskPriority(task_data["priority"])
                except ValueError:
                    pass
            if "worker_prompt" in task_data:
                task.worker_prompt = {"prompt": task_data["worker_prompt"]}
            if "qa_prompt" in task_data:
                task.qa_prompt = {"prompt": task_data["qa_prompt"]}

            actions.append({"type": "task_modified", "task_id": str(task.id), "title": task.title})

        return actions
