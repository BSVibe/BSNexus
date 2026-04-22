"""Tests for CreateScreenTool — duplicate screen prevention and fuzzy guard."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from backend.src.tools.base import ToolContext, ToolExecutionError
from backend.src.tools.design_tools import CreateScreenTool, ModifyScreenTool, SCREEN_DIR, SCREEN_EXT


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def design_ctx(tmp_path: Path) -> ToolContext:
    """Minimal ToolContext for design tools (filesystem only, no DB)."""
    return ToolContext(
        project_id=uuid.uuid4(),
        workspace_path=tmp_path,
        workspace_type="server_managed",
        agent_id=uuid.uuid4(),
        agent_name="Designer",
        tenant_id=uuid.uuid4(),
        db_session_factory=lambda: None,  # design tools don't use DB
    )


def _valid_spec(title: str = "Test") -> dict:
    """Minimal valid Screen spec that passes bsd_schema validation."""
    return {"type": "Screen", "props": {"title": title}, "children": []}


def _seed_screen(workspace: Path, slug: str, name: str = "Test Screen") -> Path:
    """Write a minimal valid .bsd screen file."""
    screen_dir = workspace / SCREEN_DIR
    screen_dir.mkdir(parents=True, exist_ok=True)
    path = screen_dir / f"{slug}{SCREEN_EXT}"
    data = {
        "name": name,
        "route": f"/{slug}",
        "intent": "test",
        "spec": _valid_spec(name),
    }
    path.write_text(json.dumps(data))
    return path


# ── Duplicate prevention tests ────────────────────────────────────────


class TestCreateScreenDuplicatePrevention:
    """CreateScreenTool should reject when a screen with the same or similar slug exists."""

    async def test_first_time_succeeds(self, design_ctx: ToolContext) -> None:
        """Creating a screen in an empty directory should succeed."""
        tool = CreateScreenTool()
        result = json.loads(await tool.execute({
            "name": "Login Page",
            "spec": _valid_spec("Login Page"),
        }, design_ctx))
        assert result["status"] == "created"
        assert result["slug"] == "login-page"

    async def test_existing_slug_raises_error(self, design_ctx: ToolContext) -> None:
        """Creating a screen when the same slug exists should raise ToolExecutionError."""
        _seed_screen(design_ctx.workspace_path, "login-page", "Login Page")

        tool = CreateScreenTool()
        with pytest.raises(ToolExecutionError, match="already exists"):
            await tool.execute({
                "name": "Login Page",
                "spec": _valid_spec("Login Page"),
            }, design_ctx)

    async def test_existing_slug_suggests_modify_screen(self, design_ctx: ToolContext) -> None:
        """Error message should mention modify_screen with the correct slug."""
        _seed_screen(design_ctx.workspace_path, "user-dashboard", "User Dashboard")

        tool = CreateScreenTool()
        with pytest.raises(ToolExecutionError, match=r'modify_screen.*user-dashboard'):
            await tool.execute({
                "name": "User Dashboard",
                "spec": _valid_spec("User Dashboard"),
            }, design_ctx)

    async def test_fuzzy_similar_raises_error(self, design_ctx: ToolContext) -> None:
        """Creating a screen with a fuzzy-similar slug (>0.7) should raise error."""
        # "user-dashboard" vs "user-dashbord" (typo) → high similarity
        _seed_screen(design_ctx.workspace_path, "user-dashboard", "User Dashboard")

        tool = CreateScreenTool()
        with pytest.raises(ToolExecutionError, match="similar screen.*user-dashboard"):
            await tool.execute({
                "name": "User Dashbord",  # typo → slug "user-dashbord"
                "spec": _valid_spec("User Dashbord"),
            }, design_ctx)

    async def test_distinct_name_succeeds(self, design_ctx: ToolContext) -> None:
        """Creating a screen with a clearly different name should succeed."""
        _seed_screen(design_ctx.workspace_path, "login-page", "Login Page")

        tool = CreateScreenTool()
        result = json.loads(await tool.execute({
            "name": "Settings Panel",
            "spec": _valid_spec("Settings Panel"),
        }, design_ctx))
        assert result["status"] == "created"
        assert result["slug"] == "settings-panel"

    async def test_modify_screen_still_works(self, design_ctx: ToolContext) -> None:
        """modify_screen should still update existing screens normally."""
        _seed_screen(design_ctx.workspace_path, "login-page", "Login Page")

        tool = ModifyScreenTool()
        result = json.loads(await tool.execute({
            "slug": "login-page",
            "intent": "Updated login with social auth",
        }, design_ctx))
        assert result["status"] == "updated"
