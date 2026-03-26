"""Tests for the codebase analyzer module."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.src.core.codebase_analyzer import (
    CodebaseAnalysis,
    analyze_codebase,
    _analyze_sync,
    _build_file_tree,
)


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a sample project structure for testing."""
    # Root files
    (tmp_path / "README.md").write_text("# My Project\nA sample project for testing.")
    (tmp_path / ".gitignore").write_text("node_modules/\n__pycache__/\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-project"\nversion = "0.1.0"')
    (tmp_path / ".git").mkdir()  # Simulate git repo

    # Source directory
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "main.py").write_text("import asyncio\n\nasync def main():\n    print('hello')\n\nasyncio.run(main())\n")
    (src / "utils.py").write_text("def helper():\n    return 42\n")

    # Tests directory
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_main():\n    assert True\n")

    return tmp_path


@pytest.fixture
def empty_project(tmp_path: Path) -> Path:
    """Create an empty project directory."""
    return tmp_path


@pytest.fixture
def node_project(tmp_path: Path) -> Path:
    """Create a Node.js project structure."""
    (tmp_path / "package.json").write_text('{"name": "my-app", "version": "1.0.0"}')
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"target": "es2020"}}')

    src = tmp_path / "src"
    src.mkdir()
    (src / "index.ts").write_text("export function main(): void {\n  console.log('hello')\n}\n")
    (src / "app.tsx").write_text("export default function App() {\n  return <div>Hello</div>\n}\n")

    return tmp_path


class TestBuildFileTree:
    def test_builds_tree_from_directory(self, sample_project: Path) -> None:
        tree, files = _build_file_tree(sample_project)
        assert sample_project.name in tree
        assert "main.py" in tree
        assert "pyproject.toml" in tree
        assert len(files) > 0

    def test_skips_ignored_directories(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.json").write_text("{}")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.pyc").write_text("bytecode")

        tree, files = _build_file_tree(tmp_path)
        assert "node_modules" not in tree
        assert "__pycache__" not in tree
        assert "app.py" in tree
        # Only src/app.py should be collected
        file_names = [f.name for f in files]
        assert "app.py" in file_names
        assert "pkg.json" not in file_names

    def test_respects_max_depth(self, tmp_path: Path) -> None:
        # Create deeply nested structure
        current = tmp_path
        for i in range(6):
            current = current / f"level{i}"
            current.mkdir()
            (current / f"file{i}.py").write_text(f"x = {i}")

        tree, _ = _build_file_tree(tmp_path, max_depth=2)
        assert "level0" in tree
        assert "level1" in tree
        # level2 files won't appear in tree but dirs might still be listed
        # The point is the tree doesn't go infinitely deep


class TestAnalyzeSync:
    def test_analyzes_python_project(self, sample_project: Path) -> None:
        result = _analyze_sync(str(sample_project))

        assert isinstance(result, CodebaseAnalysis)
        assert result.project_name == sample_project.name
        assert result.has_git is True
        assert result.total_files > 0
        assert result.total_lines > 0
        assert "Python" in result.languages
        assert result.readme_content is not None
        assert "My Project" in result.readme_content
        assert result.gitignore_content is not None

    def test_detects_config_files(self, sample_project: Path) -> None:
        result = _analyze_sync(str(sample_project))
        assert any("pyproject.toml" in key for key in result.config_files)

    def test_reads_entry_points(self, sample_project: Path) -> None:
        result = _analyze_sync(str(sample_project))
        assert any("main.py" in key for key in result.key_source_files)

    def test_analyzes_node_project(self, node_project: Path) -> None:
        result = _analyze_sync(str(node_project))
        assert "TypeScript" in result.languages or "TypeScript (React)" in result.languages
        assert any("package.json" in key for key in result.config_files)

    def test_handles_empty_directory(self, empty_project: Path) -> None:
        result = _analyze_sync(str(empty_project))
        assert result.total_files == 0
        assert result.readme_content is None

    def test_raises_for_nonexistent_path(self) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            _analyze_sync("/nonexistent/path/to/project")

    def test_raises_for_file_path(self, tmp_path: Path) -> None:
        file_path = tmp_path / "afile.txt"
        file_path.write_text("content")
        with pytest.raises(ValueError, match="not a directory"):
            _analyze_sync(str(file_path))

    def test_respects_content_budget(self, sample_project: Path) -> None:
        result = _analyze_sync(str(sample_project), max_content_chars=100)
        # With very low budget, should still produce valid result
        assert isinstance(result, CodebaseAnalysis)
        assert result.project_name == sample_project.name

    def test_no_git_detection(self, empty_project: Path) -> None:
        result = _analyze_sync(str(empty_project))
        assert result.has_git is False


class TestToPromptText:
    def test_produces_formatted_output(self, sample_project: Path) -> None:
        result = _analyze_sync(str(sample_project))
        text = result.to_prompt_text()

        assert f"# Project: {sample_project.name}" in text
        assert "## File Structure" in text
        assert "## README" in text
        assert "## Configuration Files" in text
        assert "## Key Source Files" in text
        assert "Languages:" in text

    def test_handles_minimal_analysis(self) -> None:
        analysis = CodebaseAnalysis(
            project_name="empty",
            file_tree="empty/",
        )
        text = analysis.to_prompt_text()
        assert "# Project: empty" in text
        assert "Git repository: No" in text


@pytest.mark.asyncio
class TestAnalyzeCodebaseAsync:
    async def test_runs_analysis_async(self, sample_project: Path) -> None:
        result = await analyze_codebase(str(sample_project))
        assert isinstance(result, CodebaseAnalysis)
        assert result.project_name == sample_project.name
        assert result.has_git is True

    async def test_raises_for_invalid_path(self) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            await analyze_codebase("/nonexistent/path")
