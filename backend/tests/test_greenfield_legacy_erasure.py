from __future__ import annotations

import ast
from pathlib import Path

from backend.src.main import create_app


FORBIDDEN_IMPORT_PREFIXES = (
    # G6.2 — ``core.llm`` is now the legitimate greenfield home for the
    # ``DirectLLMAdapter`` (litellm import is fenced to that package per
    # the CLAUDE.md two-path LLM dispatch MUST rule). It used to be on
    # this list because the **legacy** ``core.llm.*`` modules were
    # retired in the G0 reset, but the greenfield rebuild reuses the
    # package name. Files like ``proof.py`` / ``run_attempts.py`` still
    # don't import from it, but enforcement of the fence has moved to
    # the per-file litellm grep guard below.
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
    Path("src/core/verification.py"),
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
    # G6.2 — ``src/core/llm/direct_client.py`` is the greenfield
    # DirectLLMAdapter (Phase 2b two-path LLM dispatch). It used to be
    # on the absent-list because the legacy file was retired; the
    # rebuild is now landed.
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


def test_litellm_is_not_imported_directly_by_bsnexus():
    """CLAUDE.md MUST rule + G6.2 follow-up — BSNexus never imports
    ``litellm`` directly. ``core/llm/`` now routes through
    ``bsvibe_llm.LlmClient`` (``direct=True``), which is the shared
    BSVibe surface that owns retry / fallback / wire-contract. The
    direct ``litellm`` pin was dropped from ``pyproject.toml``; the
    package is now only a transitive dep of ``bsvibe-llm``."""
    repo = Path.cwd()
    src_root = repo / "src"
    violations: list[str] = []
    for path in src_root.rglob("*.py"):
        for imported in _imports_for(path):
            if imported == "litellm" or imported.startswith("litellm."):
                violations.append(f"{path.relative_to(repo)}: {imported}")
    assert violations == [], (
        "BSNexus must not import ``litellm`` directly — use "
        "``bsvibe_llm.LlmClient`` instead. Found leaks: " + str(violations)
    )
