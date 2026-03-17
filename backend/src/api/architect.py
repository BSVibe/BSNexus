from __future__ import annotations

import json
import re
import unicodedata
import uuid
from typing import Any

from backend.src import models, schemas
from backend.src.api.settings import get_raw_llm_config
from backend.src.core.llm_client import LLMClient, LLMConfig, LLMError
from backend.src.prompts.loader import get_prompt
from backend.src.repositories.design_session_repository import DesignSessionRepository
from backend.src.repositories.phase_repository import PhaseRepository
from backend.src.repositories.project_repository import ProjectRepository
from backend.src.repositories.task_repository import TaskRepository
from backend.src.storage.database import async_session, get_db
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

router = APIRouter(prefix="/api/v1/architect", tags=["architect"])


def _slugify(value: str) -> str:
    """Convert a string to a URL-friendly slug."""
    value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    value = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"[-\s]+", "-", value).strip("-")


FINALIZE_MARKER = "[FINALIZE]"
_CONTEXT_RE = re.compile(r"<design_context>(.*?)</design_context>", re.DOTALL)


def _clean_response(text: str) -> tuple[str, bool, str | None]:
    """Strip [FINALIZE] marker and extract design_context from text.

    Returns (cleaned_text, has_finalize, design_context).
    The cleaned_text has both the marker and design_context block removed
    so that only the user-visible portion remains.
    """
    has_finalize = FINALIZE_MARKER in text

    # Extract design_context before stripping
    design_context: str | None = None
    ctx_match = _CONTEXT_RE.search(text)
    if ctx_match:
        design_context = ctx_match.group(1).strip()

    # Strip both marker and context block from the user-visible text
    cleaned = text.replace(FINALIZE_MARKER, "")
    cleaned = _CONTEXT_RE.sub("", cleaned).strip()

    return cleaned, has_finalize, design_context


_STREAM_MARKERS = ["<design_context>", "[FINALIZE]", "[CREATE_TASK]", "[MODIFY_TASK]"]
_MAX_MARKER_LEN = max(len(m) for m in _STREAM_MARKERS)


def _find_potential_marker_start(text: str) -> int | None:
    """Find the earliest position where a suffix of *text* could be the start of a marker.

    Returns the index into *text*, or ``None`` if no suffix matches any marker
    prefix.  This is used during streaming to decide how much of the accumulated
    buffer is safe to emit.
    """
    start = max(len(text) - _MAX_MARKER_LEN, 0)
    for i in range(start, len(text)):
        suffix = text[i:]
        for marker in _STREAM_MARKERS:
            if marker.startswith(suffix):
                return i
    return None


def _extract_design_context(session: models.DesignSession) -> str | None:
    """Extract design_context from the last assistant chat message."""
    chat_messages = [
        m
        for m in session.messages
        if m.message_type == models.MessageType.chat
        and m.role == models.MessageRole.assistant
    ]
    if not chat_messages:
        return None
    last = sorted(chat_messages, key=lambda m: m.created_at)[-1]
    match = _CONTEXT_RE.search(last.content)
    return match.group(1).strip() if match else None


def _build_llm_config(llm_config_dict: dict[str, Any] | None) -> LLMConfig:
    """Build an LLMConfig from a dict stored in session.llm_config."""
    if not llm_config_dict or "api_key" not in llm_config_dict:
        raise ValueError("LLM configuration with api_key is required")
    return LLMConfig(
        api_key=llm_config_dict["api_key"],
        model=llm_config_dict.get("model") or "anthropic/claude-sonnet-4-20250514",
        base_url=llm_config_dict.get("base_url"),
    )


async def _load_project_with_tasks(project_id: uuid.UUID, db: AsyncSession) -> models.Project | None:
    """Load a project with phases and tasks eagerly loaded for context building."""
    result = await db.execute(
        select(models.Project)
        .where(models.Project.id == project_id)
        .options(
            selectinload(models.Project.phases).selectinload(models.Phase.tasks)
        )
    )
    return result.scalar_one_or_none()


def _build_project_context(project: models.Project) -> str:
    """Build a text summary of current project state for project-bound sessions."""
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


def _build_message_history(
    session: models.DesignSession, project: models.Project | None = None
) -> list[dict[str, str]]:
    """Build LLM message history from a session's messages.

    Prepends the system prompt from the prompt template with role='system',
    then appends all stored messages (user + assistant) in chronological order.
    For project_bound sessions, uses the project-aware system prompt.
    """
    if session.status == models.DesignSessionStatus.project_bound and project:
        project_context = _build_project_context(project)
        system_template = get_prompt("architect", "system_project_bound")
        system_content = system_template.format(project_context=project_context)
    else:
        system_content = get_prompt("architect", "system")

    history: list[dict[str, str]] = [
        {"role": "system", "content": system_content},
    ]
    chat_messages = [
        m for m in session.messages if m.message_type == models.MessageType.chat
    ]
    sorted_messages = sorted(chat_messages, key=lambda m: m.created_at)
    history.extend(
        {
            "role": (
                m.role.value if isinstance(m.role, models.MessageRole) else str(m.role)
            ),
            "content": m.content,
        }
        for m in sorted_messages
    )
    return history


# ── Action Markers ──────────────────────────────────────────────────

_CREATE_TASK_RE = re.compile(r"\[CREATE_TASK\](.*?)\[/CREATE_TASK\]", re.DOTALL)
_MODIFY_TASK_RE = re.compile(r"\[MODIFY_TASK\](.*?)\[/MODIFY_TASK\]", re.DOTALL)
_ACTION_MARKERS = ["[CREATE_TASK]", "[/CREATE_TASK]", "[MODIFY_TASK]", "[/MODIFY_TASK]"]


def _strip_action_markers(text: str) -> str:
    """Remove action marker blocks from user-visible text."""
    text = _CREATE_TASK_RE.sub("", text)
    text = _MODIFY_TASK_RE.sub("", text)
    return text.strip()


async def _execute_action_markers(
    text: str, session: models.DesignSession, db: AsyncSession
) -> list[dict[str, Any]]:
    """Parse and execute action markers from LLM response.

    Returns list of executed actions for board event publishing.
    """
    actions: list[dict[str, Any]] = []

    if not session.project_id:
        return actions

    task_repo = TaskRepository(db)

    # Handle CREATE_TASK markers
    for match in _CREATE_TASK_RE.finditer(text):
        try:
            task_data = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue

        # Find target phase (active phase by default)
        phase_repo = PhaseRepository(db)
        phases = await phase_repo.list_by_project(session.project_id)
        active_phase = next((p for p in phases if p.status == models.PhaseStatus.active), None)
        if not active_phase:
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
        db.add(new_task)
        await db.flush()
        actions.append({"type": "task_created", "task_id": str(new_task.id), "title": new_task.title})

    # Handle MODIFY_TASK markers
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

        # Only allow modification of tasks not yet in progress
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


# ── 0. List Sessions ─────────────────────────────────────────────────


@router.get("/sessions", response_model=list[schemas.DesignSessionResponse])
async def list_sessions(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[schemas.DesignSessionResponse]:
    """List all design sessions, optionally filtered by status."""
    repo = DesignSessionRepository(db)
    status_filter = None
    if status is not None:
        try:
            status_filter = models.DesignSessionStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    sessions = await repo.list_sessions(status=status_filter)
    for s in sessions:
        s.messages = [
            m for m in s.messages if m.message_type == models.MessageType.chat
        ]
    return [schemas.DesignSessionResponse.model_validate(s) for s in sessions]


# ── 0b. Delete Session ──────────────────────────────────────────────


@router.delete("/sessions/{session_id}", response_model=schemas.DeleteResponse)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> schemas.DeleteResponse:
    """Delete a design session and all its messages."""
    repo = DesignSessionRepository(db)
    session = await repo.get_by_id(session_id, load_messages=False)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    await repo.delete(session)
    await repo.commit()

    return schemas.DeleteResponse(detail="Session deleted")


# ── 0c. Batch Delete Sessions ──────────────────────────────────────


@router.post("/sessions/batch-delete", response_model=schemas.BatchDeleteResponse)
async def batch_delete_sessions(
    body: schemas.BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.BatchDeleteResponse:
    """Delete multiple design sessions by IDs."""
    from sqlalchemy import delete as sa_delete

    cursor = await db.execute(
        sa_delete(models.DesignSession).where(models.DesignSession.id.in_(body.ids))
    )
    await db.commit()
    deleted: int = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0  # type: ignore[attr-defined]
    return schemas.BatchDeleteResponse(deleted=deleted)


# ── 1. Create Session ────────────────────────────────────────────────


@router.post("/sessions", response_model=schemas.DesignSessionResponse, status_code=201)
async def create_session(
    body: schemas.CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.DesignSessionResponse:
    """Create a new design session using global LLM settings."""
    settings = await get_raw_llm_config(db)
    api_key = settings.get("llm_api_key")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="LLM API key not configured. Set it in Settings first.",
        )

    llm_config_dict: dict[str, Any] = {"api_key": api_key}
    if settings.get("llm_model"):
        llm_config_dict["model"] = settings["llm_model"]
    if settings.get("llm_base_url"):
        llm_config_dict["base_url"] = settings["llm_base_url"]

    repo = DesignSessionRepository(db)
    session = await repo.add(
        models.DesignSession(name=body.name, llm_config=llm_config_dict, worker_id=body.worker_id)
    )
    await repo.commit()

    # Reload with messages
    loaded = await repo.get_by_id(session.id)
    if loaded is None:
        raise HTTPException(status_code=500, detail="Failed to create session")
    return schemas.DesignSessionResponse.model_validate(loaded)


# ── 2. Get Session ───────────────────────────────────────────────────


@router.get("/sessions/by-project/{project_id}", response_model=schemas.DesignSessionResponse)
async def get_session_by_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> schemas.DesignSessionResponse:
    """Get the project-bound session for a given project."""
    repo = DesignSessionRepository(db)
    session = await repo.get_by_project_id(project_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No session found for this project")
    session.messages = [
        m for m in session.messages if m.message_type == models.MessageType.chat
    ]
    return schemas.DesignSessionResponse.model_validate(session)


@router.get("/sessions/{session_id}", response_model=schemas.DesignSessionResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> schemas.DesignSessionResponse:
    """Get a design session with all messages."""
    repo = DesignSessionRepository(db)
    session = await repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.messages = [
        m for m in session.messages if m.message_type == models.MessageType.chat
    ]
    return schemas.DesignSessionResponse.model_validate(session)


# ── 3. REST Message (non-streaming) ─────────────────────────────────


@router.post(
    "/sessions/{session_id}/message", response_model=schemas.DesignMessageResponse
)
async def send_message(
    session_id: uuid.UUID,
    body: schemas.MessageRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.DesignMessageResponse:
    """Send a message and get a non-streaming LLM response."""
    repo = DesignSessionRepository(db)
    session = await repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status == models.DesignSessionStatus.cancelled:
        raise HTTPException(status_code=400, detail="Session is cancelled")

    # Load project for project_bound sessions
    project: models.Project | None = None
    if session.status == models.DesignSessionStatus.project_bound and session.project_id:
        project = await _load_project_with_tasks(session.project_id, db)

    # Save user message
    await repo.add_message(session.id, models.MessageRole.user, body.content)

    # Build message history and call LLM
    messages = _build_message_history(session, project=project)
    messages.append({"role": "user", "content": body.content})

    config = _build_llm_config(session.llm_config)
    client = LLMClient(config)

    try:
        response_text = await client.chat(messages)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    # Detect and strip finalize marker / design_context
    cleaned_text, has_finalize, design_context = _clean_response(response_text)

    # Execute action markers for project-bound sessions
    if session.status == models.DesignSessionStatus.project_bound:
        await _execute_action_markers(response_text, session, db)
        cleaned_text = _strip_action_markers(cleaned_text)

    # Save assistant response (original with design_context for later extraction)
    assistant_msg = await repo.add_message(
        session.id,
        models.MessageRole.assistant,
        response_text.replace(FINALIZE_MARKER, "").strip(),
    )
    await repo.commit()
    await repo.refresh(assistant_msg)

    response = schemas.DesignMessageResponse.model_validate(assistant_msg)
    response.content = cleaned_text  # Strip design_context + action markers from frontend response
    response.finalize_ready = has_finalize
    response.design_context = design_context
    return response


# ── 4. SSE Streaming Message ────────────────────────────────────────


@router.post("/sessions/{session_id}/message/stream")
async def send_message_stream(
    session_id: uuid.UUID,
    body: schemas.MessageRequest,
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """Send a message and stream the LLM response via SSE."""
    repo = DesignSessionRepository(db)
    session = await repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status == models.DesignSessionStatus.cancelled:
        raise HTTPException(status_code=400, detail="Session is cancelled")

    # Load project for project_bound sessions
    project: models.Project | None = None
    is_project_bound = session.status == models.DesignSessionStatus.project_bound
    if is_project_bound and session.project_id:
        project = await _load_project_with_tasks(session.project_id, db)

    # Save user message
    await repo.add_message(session.id, models.MessageRole.user, body.content)

    # Build message history
    messages = _build_message_history(session, project=project)
    messages.append({"role": "user", "content": body.content})

    config = _build_llm_config(session.llm_config)
    client = LLMClient(config)

    # Capture session state for use in event generator closure
    session_id_val = session.id
    session_project_id = session.project_id

    async def event_generator():
        full_response = ""
        emit_buffer = ""
        suppressed = False

        try:
            async for chunk in client.stream_chat(messages):
                full_response += chunk

                if suppressed:
                    # Already found marker start — just accumulate for DB
                    continue

                emit_buffer += chunk

                # Check for full <design_context> tag
                dc_idx = emit_buffer.find("<design_context>")
                if dc_idx != -1:
                    safe_text = emit_buffer[:dc_idx].rstrip()
                    if safe_text:
                        yield {"event": "chunk", "data": safe_text}
                    emit_buffer = ""
                    suppressed = True
                    continue

                # Check for [FINALIZE] on its own
                fin_idx = emit_buffer.find(FINALIZE_MARKER)
                if fin_idx != -1:
                    safe_text = emit_buffer[:fin_idx].rstrip()
                    if safe_text:
                        yield {"event": "chunk", "data": safe_text}
                    emit_buffer = ""
                    suppressed = True
                    continue

                # Check for action markers in project-bound mode
                if is_project_bound:
                    for marker in _ACTION_MARKERS:
                        marker_idx = emit_buffer.find(marker)
                        if marker_idx != -1:
                            safe_text = emit_buffer[:marker_idx].rstrip()
                            if safe_text:
                                yield {"event": "chunk", "data": safe_text}
                            emit_buffer = ""
                            suppressed = True
                            break
                    if suppressed:
                        continue

                # Check if the tail could be the start of a marker
                marker_start = _find_potential_marker_start(emit_buffer)
                if marker_start is not None:
                    safe_text = emit_buffer[:marker_start]
                    if safe_text:
                        yield {"event": "chunk", "data": safe_text}
                    emit_buffer = emit_buffer[marker_start:]
                else:
                    if emit_buffer:
                        yield {"event": "chunk", "data": emit_buffer}
                    emit_buffer = ""

        except LLMError as e:
            yield {"event": "error", "data": str(e)}
            return

        # Flush remaining buffer that turned out not to be a marker
        if emit_buffer and not suppressed:
            cleaned_buf = emit_buffer.replace(FINALIZE_MARKER, "")
            cleaned_buf = _CONTEXT_RE.sub("", cleaned_buf).strip()
            if is_project_bound:
                cleaned_buf = _strip_action_markers(cleaned_buf)
            if cleaned_buf:
                yield {"event": "chunk", "data": cleaned_buf}

        # Detect and strip finalize marker / design_context
        cleaned_response, has_finalize, design_context = _clean_response(full_response)
        if is_project_bound:
            cleaned_response = _strip_action_markers(cleaned_response)

        # Execute action markers for project-bound sessions
        if is_project_bound and session_project_id:
            async with async_session() as action_db:
                # Re-load session for action execution
                action_session = models.DesignSession(
                    id=session_id_val, project_id=session_project_id
                )
                actions = await _execute_action_markers(full_response, action_session, action_db)
                await action_db.commit()
                if actions:
                    yield {"event": "actions_executed", "data": json.dumps(actions)}

        # Save assistant response (with design_context preserved for finalize extraction)
        stored_response = full_response.replace(FINALIZE_MARKER, "").strip()
        await repo.add_message(
            session_id_val, models.MessageRole.assistant, stored_response
        )
        await repo.commit()

        yield {"event": "done", "data": cleaned_response}

        if has_finalize:
            yield {"event": "finalize_ready", "data": design_context or ""}

    return EventSourceResponse(event_generator())


# ── 5. Finalize Design ──────────────────────────────────────────────


@router.post("/sessions/{session_id}/finalize", response_model=schemas.ProjectResponse)
async def finalize_design(
    session_id: uuid.UUID,
    body: schemas.FinalizeRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.ProjectResponse:
    """Finalize a design session into a project with phases and tasks.

    Splits into two DB session scopes to avoid holding a connection idle
    during the long-running LLM call (which can take 30-60+ seconds and
    cause intermittent stale-connection errors).
    """
    # ── Phase 1: Read session & prepare LLM messages (short DB scope) ──
    session_repo = DesignSessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status == models.DesignSessionStatus.project_bound:
        # Already finalized — return the existing project instead of 400
        if session.project_id:
            project_repo = ProjectRepository(db)
            existing_project = await project_repo.get_by_id(session.project_id)
            if existing_project:
                return schemas.ProjectResponse.model_validate(existing_project)
        raise HTTPException(
            status_code=400, detail="Session is finalized but project not found"
        )

    if session.status != models.DesignSessionStatus.active:
        raise HTTPException(status_code=400, detail="Session is not active")

    # Extract everything we need from the session before releasing the DB
    design_context = _extract_design_context(session)
    llm_config_dict = session.llm_config
    session_worker_id = session.worker_id

    finalize_template = get_prompt("architect", "finalize")
    if design_context:
        finalize_prompt = finalize_template.format(design_context=design_context)
        messages = [
            {"role": "system", "content": get_prompt("architect", "system")},
            {"role": "user", "content": finalize_prompt},
        ]
    else:
        finalize_prompt = finalize_template.format(
            design_context="(see conversation history above)"
        )
        messages = _build_message_history(session)
        messages.append({"role": "user", "content": finalize_prompt})

    # ── Phase 2: LLM call (DB not used — reads are done) ────────────────
    config = _build_llm_config(llm_config_dict)
    client = LLMClient(config)

    try:
        result = await client.structured_output(
            messages=messages,
            response_format={"type": "json_object"},
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    # ── Phase 3: Write results with a fresh DB session ─────────────────
    async with async_session() as write_db:
        write_session_repo = DesignSessionRepository(write_db)

        # Re-validate session status (guard against concurrent finalize)
        session = await write_session_repo.get_by_id(session_id, load_messages=False)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status == models.DesignSessionStatus.project_bound:
            if session.project_id:
                project_repo = ProjectRepository(write_db)
                existing_project = await project_repo.get_by_id(session.project_id)
                if existing_project:
                    return schemas.ProjectResponse.model_validate(existing_project)
            raise HTTPException(
                status_code=400, detail="Session is finalized but project not found"
            )

        # Store finalize prompt and response as internal messages for auditability
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

        # Build llm_config for the project
        project_llm_config: dict[str, Any] = {
            "architect": llm_config_dict,
        }
        if body.pm_llm_config:
            project_llm_config["pm"] = {
                "api_key": body.pm_llm_config.api_key,
            }
            if body.pm_llm_config.model:
                project_llm_config["pm"]["model"] = body.pm_llm_config.model
            if body.pm_llm_config.base_url:
                project_llm_config["pm"]["base_url"] = body.pm_llm_config.base_url

        # Create project
        project = models.Project(
            name=result.get("project_name", "Untitled Project"),
            description=result.get("project_description", ""),
            repo_path=body.repo_path,
            status=models.ProjectStatus.design,
            llm_config=project_llm_config,
        )
        write_db.add(project)
        await write_db.flush()

        # Create phases and tasks, collecting all tasks in a flat list for dependency resolution
        all_tasks: list[models.Task] = []
        phases_data = result.get("phases", [])
        phases_by_order: dict[int, models.Phase] = {}

        # Track which flat indices belong to each phase
        phase_task_indices: dict[int, set[int]] = {}  # phase_order -> set of flat indices
        flat_idx_counter = 0

        for phase_order, phase_data in enumerate(phases_data, start=1):
            phase_name = phase_data.get("name", f"Phase {phase_order}")
            branch_name = f"phase/{_slugify(phase_name)}"

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

        # Wire up dependencies using depends_on_indices
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

        # Phase-gated task status: only first phase is active, its dep-free tasks are ready
        if not phases_by_order:
            raise HTTPException(status_code=400, detail="Design must contain at least one phase with tasks")

        first_order = min(phases_by_order.keys())
        phases_by_order[first_order].status = models.PhaseStatus.active
        first_phase_indices = phase_task_indices.get(first_order, set())
        for i, task in enumerate(all_tasks):
            if i not in tasks_with_deps and i in first_phase_indices:
                task.status = models.TaskStatus.ready
            # All other tasks remain in default waiting status

        # Assign worker to project if session had a worker selected
        if session_worker_id:
            worker_result = await write_db.execute(
                select(models.Worker).where(models.Worker.id == session_worker_id)
            )
            worker = worker_result.scalar_one_or_none()
            if worker:
                worker.project_id = project.id

        # Update session status and link to project
        session.status = models.DesignSessionStatus.project_bound
        session.project_id = project.id
        await write_db.commit()

        # Reload project with phases
        project_repo = ProjectRepository(write_db)
        loaded_project = await project_repo.get_by_id(project.id)
        return schemas.ProjectResponse.model_validate(loaded_project)


# ── 6. Redesign Phase ──────────────────────────────────────────────


@router.post("/redesign/phase/{phase_id}", response_model=schemas.PhaseRedesignResponse)
async def redesign_phase(
    phase_id: uuid.UUID,
    body: schemas.PhaseRedesignRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.PhaseRedesignResponse:
    """Trigger manual phase-level redesign via Architect LLM.

    Used when auto-redesign has failed and user intervention is needed.
    Sends all incomplete tasks to the Architect LLM for redesign.
    """
    from backend.src.core.llm_client import create_llm_client_from_project
    from backend.src.core.state_machine import TaskStateMachine

    phase_repo = PhaseRepository(db)
    phase = await phase_repo.get_by_id(phase_id)
    if phase is None:
        raise HTTPException(status_code=404, detail="Phase not found")

    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(phase.project_id, load_phases=False)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check that there are redesign tasks in this phase
    task_repo = TaskRepository(db)
    incomplete_tasks = await task_repo.list_incomplete_in_phase(phase_id)
    redesign_tasks = [t for t in incomplete_tasks if t.status == models.TaskStatus.redesign]
    if not redesign_tasks:
        raise HTTPException(status_code=400, detail="No tasks in redesign status in this phase")

    # Build LLM client — prefer request body config, then project config
    try:
        if body.llm_config:
            llm_config_dict: dict[str, Any] = {"api_key": body.llm_config.api_key}
            if body.llm_config.model:
                llm_config_dict["model"] = body.llm_config.model
            if body.llm_config.base_url:
                llm_config_dict["base_url"] = body.llm_config.base_url
            config = _build_llm_config(llm_config_dict)
            client = LLMClient(config)
        else:
            client = create_llm_client_from_project(project, role="architect")
    except (ValueError, LLMError) as e:
        raise HTTPException(status_code=400, detail=f"LLM configuration error: {e}") from e

    # Build context
    done_tasks = await task_repo.list_done_in_phase(phase_id)

    def _task_to_dict(t: models.Task) -> dict[str, Any]:
        wp = ""
        if t.worker_prompt:
            wp = t.worker_prompt.get("prompt", json.dumps(t.worker_prompt)) if isinstance(t.worker_prompt, dict) else str(t.worker_prompt)
        qp = ""
        if t.qa_prompt:
            qp = t.qa_prompt.get("prompt", json.dumps(t.qa_prompt)) if isinstance(t.qa_prompt, dict) else str(t.qa_prompt)
        dep_ids = [str(d.id) for d in t.depends_on] if t.depends_on else []
        return {
            "id": str(t.id), "title": t.title, "description": t.description or "",
            "priority": t.priority.value, "status": t.status.value,
            "worker_prompt": wp, "qa_prompt": qp, "depends_on": dep_ids,
            "retry_count": t.retry_count, "error_message": t.error_message or "",
        }

    # Use the first redesign task as the "trigger" for context
    trigger_task = redesign_tasks[0]
    failure_history = json.dumps(trigger_task.qa_feedback_history or [], indent=2)

    incomplete_dicts = [_task_to_dict(t) for t in incomplete_tasks]
    done_dicts = [{"id": str(t.id), "title": t.title, "status": "done"} for t in done_tasks]

    prompt = get_prompt("architect", "phase_redesign").format(
        failed_task_title=trigger_task.title,
        failed_task_error=trigger_task.error_message or "No error message",
        failed_task_history=failure_history,
        done_tasks=json.dumps(done_dicts, indent=2, ensure_ascii=False),
        incomplete_tasks=json.dumps(incomplete_dicts, indent=2, ensure_ascii=False),
        phase_name=phase.name,
        branch_name=phase.branch_name or "N/A",
    )

    llm_messages = [
        {"role": "system", "content": get_prompt("architect", "system")},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await client.structured_output(
            messages=llm_messages,
            response_format={"type": "json_object"},
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    reasoning = result.get("reasoning", "")
    new_task_list = result.get("tasks", [])
    if not isinstance(new_task_list, list):
        raise HTTPException(status_code=502, detail="LLM returned invalid tasks format")

    # Apply phase redesign via diff
    state_machine = TaskStateMachine()
    existing_by_id = {str(t.id): t for t in incomplete_tasks}
    result_ids: set[str] = set()
    new_tasks_data: list[dict[str, Any]] = []

    for item in new_task_list:
        item_id = item.get("id", "")
        if item_id and item_id in existing_by_id:
            result_ids.add(item_id)
        else:
            new_tasks_data.append(item)

    # Delete tasks not in the result
    to_delete = [t for tid, t in existing_by_id.items() if tid not in result_ids]
    tasks_deleted = 0
    if to_delete:
        delete_ids = [t.id for t in to_delete]
        tasks_deleted = await task_repo.hard_delete_many(delete_ids)

    # Update kept tasks
    tasks_kept = 0
    for item in new_task_list:
        item_id = item.get("id", "")
        if not item_id or item_id not in existing_by_id:
            continue
        task = existing_by_id[item_id]
        if item.get("title"):
            task.title = item["title"]
        if item.get("description"):
            task.description = item["description"]
        if item.get("worker_prompt"):
            task.worker_prompt = {"prompt": item["worker_prompt"]}
        if item.get("qa_prompt"):
            task.qa_prompt = {"prompt": item["qa_prompt"]}
        if item.get("priority"):
            try:
                task.priority = models.TaskPriority(item["priority"])
            except ValueError:
                pass
        task.retry_count = 0
        task.qa_feedback_history = None
        task.error_message = None
        task.commit_hash = None
        task.worker_id = None
        task.reviewer_id = None
        task.started_at = None
        await state_machine.transition(
            task=task,
            new_status=models.TaskStatus.waiting,
            reason=f"Manual phase redesign: {reasoning}",
            actor="architect",
            db_session=db,
        )
        tasks_kept += 1

    # Create new tasks
    tasks_created = 0
    created_tasks: dict[str, models.Task] = {}
    for item in new_tasks_data:
        priority_str = item.get("priority", "medium")
        try:
            priority = models.TaskPriority(priority_str)
        except ValueError:
            priority = models.TaskPriority.medium

        new_task = models.Task(
            project_id=phase.project_id,
            phase_id=phase.id,
            title=item.get("title", "Untitled Task"),
            description=item.get("description"),
            priority=priority,
            status=models.TaskStatus.waiting,
            worker_prompt={"prompt": item.get("worker_prompt", "")},
            qa_prompt={"prompt": item.get("qa_prompt", "")},
            branch_name=phase.branch_name,
        )
        db.add(new_task)
        await db.flush()
        created_tasks[new_task.title] = new_task
        tasks_created += 1

    # Wire up dependencies
    all_tasks_by_id: dict[str, models.Task] = {}
    all_tasks_by_title: dict[str, models.Task] = {}
    for tid, t in existing_by_id.items():
        if tid in result_ids:
            all_tasks_by_id[tid] = t
            all_tasks_by_title[t.title] = t
    for title, t in created_tasks.items():
        all_tasks_by_id[str(t.id)] = t
        all_tasks_by_title[title] = t

    for item in new_task_list:
        item_id = item.get("id", "")
        item_title = item.get("title", "")
        task_obj = all_tasks_by_id.get(item_id) or all_tasks_by_title.get(item_title)
        if not task_obj:
            continue
        depends_on_refs = item.get("depends_on", [])
        if depends_on_refs:
            await task_repo.clear_dependencies(task_obj.id)
            dep_ids: list[uuid.UUID] = []
            for ref in depends_on_refs:
                dep = all_tasks_by_id.get(ref) or all_tasks_by_title.get(ref)
                if dep:
                    dep_ids.append(dep.id)
            if dep_ids:
                await task_repo.add_dependencies(task_obj.id, dep_ids)

    # Clear intervention flags for redesign tasks in this phase
    for t in redesign_tasks:
        intervention_key = f"task:{t.id}:needs_intervention"
        try:
            from backend.src.storage.redis_client import get_redis
            redis = await get_redis()
            await redis.delete(intervention_key)
        except Exception:
            pass  # Redis cleanup is best-effort

    await db.commit()

    return schemas.PhaseRedesignResponse(
        phase_id=phase_id,
        project_id=phase.project_id,
        reasoning=reasoning,
        tasks_kept=tasks_kept,
        tasks_deleted=tasks_deleted,
        tasks_created=tasks_created,
    )


# ── 7. Add Task to Existing Project ─────────────────────────────────


@router.post(
    "/add-task/{project_id}", response_model=schemas.AddTaskResponse, status_code=201
)
async def add_task(
    project_id: uuid.UUID,
    body: schemas.AddTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.AddTaskResponse:
    """Add a task to an existing project using LLM."""
    project_repo = ProjectRepository(db)
    project = await project_repo.get_by_id(project_id)

    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    # Verify phase belongs to project
    phase_repo = PhaseRepository(db)
    phase = await phase_repo.get_by_id(body.phase_id)

    if phase is None:
        raise HTTPException(status_code=404, detail="Phase not found")

    if phase.project_id != project.id:
        raise HTTPException(
            status_code=400, detail="Phase does not belong to this project"
        )

    # Get project context (existing phases + tasks)
    phases = await phase_repo.list_by_project(project_id)
    task_repo = TaskRepository(db)
    existing_tasks = await task_repo.list_by_project(project_id)

    context = f"Project: {project.name}\nDescription: {project.description}\n\nExisting phases:\n"
    for p in phases:
        context += f"- {p.name}: {p.description or 'No description'}\n"
    context += "\nExisting tasks:\n"
    for t in existing_tasks:
        context += (
            f"- [{t.priority.value}] {t.title}: {t.description or 'No description'}\n"
        )

    prompt = get_prompt("architect", "add_task").format(
        context=context,
        phase_name=phase.name,
        request_text=body.request_text,
    )

    # Determine LLM config: override from request body, project config, or fail
    llm_config_dict: dict[str, Any] | None = None
    if body.llm_config:
        llm_config_dict = {"api_key": body.llm_config.api_key}
        if body.llm_config.model:
            llm_config_dict["model"] = body.llm_config.model
        if body.llm_config.base_url:
            llm_config_dict["base_url"] = body.llm_config.base_url
    elif project.llm_config and project.llm_config.get("architect"):
        llm_config_dict = project.llm_config["architect"]

    if not llm_config_dict or not llm_config_dict.get("api_key"):
        raise HTTPException(
            status_code=400,
            detail="No LLM configuration available. Provide llm_config in request or configure project.",
        )

    config = _build_llm_config(llm_config_dict)
    client = LLMClient(config)

    messages = [
        {"role": "system", "content": get_prompt("architect", "system")},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await client.structured_output(
            messages=messages,
            response_format={"type": "json_object"},
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    priority_str = result.get("priority", "medium")
    try:
        priority = models.TaskPriority(priority_str)
    except ValueError:
        priority = models.TaskPriority.medium

    task = models.Task(
        project_id=project.id,
        phase_id=phase.id,
        title=result.get("title", "Untitled Task"),
        description=result.get("description"),
        priority=priority,
        status=models.TaskStatus.ready,
        worker_prompt={"prompt": result.get("worker_prompt", "")},
        qa_prompt={"prompt": result.get("qa_prompt", "")},
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    return schemas.AddTaskResponse.model_validate(task)
