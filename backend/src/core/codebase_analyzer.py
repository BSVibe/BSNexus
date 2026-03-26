"""Codebase Analyzer — scans a project folder and produces a structured summary for LLM consumption."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

# Directories to always skip
_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".eggs", "dist", "build",
    ".next", ".nuxt", ".output", "target", "vendor", ".cargo",
    "coverage", ".coverage", "htmlcov", ".terraform", ".serverless",
})

# Config files to auto-detect and read
_CONFIG_FILES = frozenset({
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "go.sum",
    "pom.xml", "build.gradle", "Gemfile", "Makefile", "CMakeLists.txt",
    "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
    ".env.example", "tsconfig.json", "vite.config.ts", "vite.config.js",
    "next.config.js", "next.config.mjs", "webpack.config.js",
    "tailwind.config.js", "tailwind.config.ts",
})

# Entry point files to prioritize reading
_ENTRY_POINTS = frozenset({
    "main.py", "app.py", "server.py", "index.ts", "index.js",
    "main.ts", "main.js", "main.go", "main.rs", "lib.rs",
    "manage.py", "wsgi.py", "asgi.py",
})

# Language detection by extension
_LANG_EXTENSIONS: dict[str, str] = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript (React)",
    ".js": "JavaScript", ".jsx": "JavaScript (React)",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".cpp": "C++",
    ".c": "C", ".swift": "Swift", ".dart": "Dart",
    ".vue": "Vue", ".svelte": "Svelte",
    ".sql": "SQL", ".sh": "Shell", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".json": "JSON", ".md": "Markdown",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
}


@dataclass
class CodebaseAnalysis:
    """Structured analysis result of a codebase."""

    project_name: str
    file_tree: str
    total_files: int = 0
    total_lines: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    readme_content: str | None = None
    config_files: dict[str, str] = field(default_factory=dict)
    key_source_files: dict[str, str] = field(default_factory=dict)
    gitignore_content: str | None = None
    has_git: bool = False

    def to_prompt_text(self) -> str:
        """Format the analysis as structured text for LLM consumption."""
        sections: list[str] = []

        sections.append(f"# Project: {self.project_name}")
        sections.append(f"Total files: {self.total_files} | Total lines: {self.total_lines}")

        if self.languages:
            sorted_langs = sorted(self.languages.items(), key=lambda x: x[1], reverse=True)
            lang_str = ", ".join(f"{lang} ({count})" for lang, count in sorted_langs[:10])
            sections.append(f"Languages: {lang_str}")

        sections.append(f"Git repository: {'Yes' if self.has_git else 'No'}")
        sections.append("")

        sections.append("## File Structure")
        sections.append("```")
        sections.append(self.file_tree)
        sections.append("```")

        if self.readme_content:
            sections.append("\n## README")
            sections.append(self.readme_content)

        if self.config_files:
            sections.append("\n## Configuration Files")
            for name, content in self.config_files.items():
                sections.append(f"\n### {name}")
                sections.append(f"```\n{content}\n```")

        if self.key_source_files:
            sections.append("\n## Key Source Files")
            for path, content in self.key_source_files.items():
                sections.append(f"\n### {path}")
                sections.append(f"```\n{content}\n```")

        return "\n".join(sections)


def _should_skip_dir(name: str) -> bool:
    """Check if a directory should be skipped."""
    return name in _SKIP_DIRS or name.startswith(".")


def _build_file_tree(root: Path, max_depth: int = 4) -> tuple[str, list[Path]]:
    """Build an indented file tree string and collect all file paths."""
    lines: list[str] = []
    all_files: list[Path] = []

    def _walk(current: Path, depth: int, prefix: str = "") -> None:
        if depth > max_depth:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return

        dirs = [e for e in entries if e.is_dir() and not _should_skip_dir(e.name)]
        files = [e for e in entries if e.is_file()]

        for f in files:
            all_files.append(f)
            if depth <= max_depth:
                lines.append(f"{prefix}{f.name}")

        for d in dirs:
            lines.append(f"{prefix}{d.name}/")
            _walk(d, depth + 1, prefix + "  ")

    lines.append(f"{root.name}/")
    _walk(root, 1, "  ")
    return "\n".join(lines), all_files


def _safe_read_text(path: Path, max_chars: int = 5000) -> str | None:
    """Read a text file safely, returning None on error."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            return content[:max_chars] + f"\n... (truncated, {len(content)} total chars)"
        return content
    except (OSError, UnicodeDecodeError):
        return None


def _count_lines(path: Path) -> int:
    """Count lines in a file safely."""
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
    except (OSError, UnicodeDecodeError):
        return 0


def _detect_language(path: Path) -> str | None:
    """Detect language from file extension."""
    return _LANG_EXTENSIONS.get(path.suffix.lower())


def _is_source_file(path: Path) -> bool:
    """Check if a file is a source code file (not binary, not generated)."""
    source_extensions = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
        ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp", ".swift", ".dart",
        ".vue", ".svelte",
    }
    return path.suffix.lower() in source_extensions


def _analyze_sync(repo_path: str, max_content_chars: int = 50000) -> CodebaseAnalysis:
    """Synchronous implementation of codebase analysis."""
    root = Path(repo_path)
    if not root.exists():
        raise ValueError(f"Path does not exist: {repo_path}")
    if not root.is_dir():
        raise ValueError(f"Path is not a directory: {repo_path}")

    project_name = root.name
    has_git = (root / ".git").exists()

    # Build file tree
    file_tree, all_files = _build_file_tree(root)

    # Count files and lines, detect languages
    total_files = len(all_files)
    total_lines = 0
    languages: dict[str, int] = {}

    for f in all_files:
        lang = _detect_language(f)
        if lang:
            languages[lang] = languages.get(lang, 0) + 1
            total_lines += _count_lines(f)

    # Budget tracking for content
    content_budget = max_content_chars
    consumed = 0

    # Read README
    readme_content: str | None = None
    for readme_name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        readme_path = root / readme_name
        if readme_path.exists():
            readme_content = _safe_read_text(readme_path, max_chars=5000)
            if readme_content:
                consumed += len(readme_content)
            break

    # Read .gitignore
    gitignore_content: str | None = None
    gitignore_path = root / ".gitignore"
    if gitignore_path.exists():
        gitignore_content = _safe_read_text(gitignore_path, max_chars=2000)
        if gitignore_content:
            consumed += len(gitignore_content)

    # Read config files
    config_files: dict[str, str] = {}
    for f in all_files:
        if f.name in _CONFIG_FILES and consumed < content_budget:
            content = _safe_read_text(f, max_chars=3000)
            if content:
                rel = str(f.relative_to(root))
                config_files[rel] = content
                consumed += len(content)

    # Read key source files (entry points + important files)
    key_source_files: dict[str, str] = {}
    max_key_files = 10
    per_file_limit = 3000

    # Prioritize entry points
    entry_point_files: list[Path] = []
    other_source_files: list[Path] = []

    for f in all_files:
        if not _is_source_file(f):
            continue
        if f.name in _ENTRY_POINTS:
            entry_point_files.append(f)
        else:
            other_source_files.append(f)

    # Sort other source files by path depth (shallower = more important)
    other_source_files.sort(key=lambda p: (len(p.parts), p.name))

    candidates = entry_point_files + other_source_files
    for f in candidates:
        if len(key_source_files) >= max_key_files:
            break
        if consumed >= content_budget:
            break
        content = _safe_read_text(f, max_chars=per_file_limit)
        if content and len(content.strip()) > 0:
            rel = str(f.relative_to(root))
            key_source_files[rel] = content
            consumed += len(content)

    return CodebaseAnalysis(
        project_name=project_name,
        file_tree=file_tree,
        total_files=total_files,
        total_lines=total_lines,
        languages=languages,
        readme_content=readme_content,
        config_files=config_files,
        key_source_files=key_source_files,
        gitignore_content=gitignore_content,
        has_git=has_git,
    )


async def analyze_codebase(repo_path: str, *, max_content_chars: int = 30000) -> CodebaseAnalysis:
    """Analyze a codebase and produce a structured summary.

    Runs file I/O in a thread pool to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_analyze_sync, repo_path, max_content_chars)
