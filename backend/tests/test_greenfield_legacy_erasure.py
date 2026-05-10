from __future__ import annotations

import ast
from pathlib import Path

from backend.src.main import create_app


FORBIDDEN_IMPORT_PREFIXES = (
    "backend.src.core.llm",
    "backend.src.core.dispatcher",
    "backend.src.core.harness",
    "backend.src.core.run_artifacts",
    "backend.src.core.run_orchestrator",
    "backend.src.core.prompts",
    "backend.src.core.verifier",
    "backend.src.mcp",
)

GREENFIELD_FILES = (
    Path("src/core/domain.py"),
    Path("src/core/state_machines.py"),
    Path("src/core/brief.py"),
    Path("src/core/deliverables.py"),
    Path("src/core/directions.py"),
    Path("src/core/proof.py"),
    Path("src/core/run_attempts.py"),
    Path("src/core/work_steps.py"),
    Path("src/api/directions.py"),
    Path("src/api/requests_api.py"),
    Path("src/api/decisions.py"),
    Path("src/api/deliverables.py"),
    Path("src/api/brief.py"),
    Path("src/models/__init__.py"),
)

LEGACY_ROUTE_NAMES = {
    "/api/v1/messages",
    "/api/v1/executor-configs",
    "/api/v1/inside/runs",
    "/api/v1/run-summaries",
    "/mcp/http",
}

ABSENT_LEGACY_FILES = (
    Path("src/api/conversation.py"),
    Path("src/api/executor_configs.py"),
    Path("src/api/inside.py"),
    Path("src/api/run_summaries.py"),
    Path("src/core/dispatcher.py"),
    Path("src/core/harness.py"),
    Path("src/core/llm/direct_client.py"),
    Path("src/core/run_artifacts.py"),
    Path("src/core/run_orchestrator.py"),
    Path("src/core/tools.py"),
    Path("src/core/verifier/subprocess_verifier.py"),
    Path("src/mcp/server.py"),
    Path("src/mcp/tools.py"),
    Path("src/models/execution_run.py"),
    Path("src/models/conversation.py"),
)


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_greenfield_files_do_not_import_forbidden_legacy_modules():
    repo = Path.cwd()
    violations: list[str] = []
    for relative_path in GREENFIELD_FILES:
        path = repo / relative_path
        assert path.exists(), f"Missing greenfield file: {relative_path}"
        for imported in _imports_for(path):
            if imported.startswith(FORBIDDEN_IMPORT_PREFIXES):
                violations.append(f"{relative_path}: {imported}")

    assert violations == []


def test_legacy_conflicting_routes_are_not_mounted():
    app = create_app(cors_origins=[], rate_limit=False)
    mounted_paths = {getattr(route, "path", "") for route in app.routes}

    for legacy_path in LEGACY_ROUTE_NAMES:
        assert legacy_path not in mounted_paths

    assert "/api/v1/directions" in mounted_paths
    assert "/api/v1/requests" in mounted_paths
    assert "/api/v1/decisions" in mounted_paths
    assert "/api/v1/deliverables" in mounted_paths
    assert "/api/v1/brief" in mounted_paths


def test_known_legacy_runtime_files_are_removed():
    repo = Path.cwd()
    still_present = [str(path) for path in ABSENT_LEGACY_FILES if (repo / path).exists()]
    assert still_present == []
