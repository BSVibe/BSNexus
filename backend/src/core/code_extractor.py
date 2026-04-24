"""Extract fenced code blocks from a run's output into (path, content).

The LLM emits answers like::

    Some prose intro.

    ### `backend/main.py`
    ```python
    from fastapi import FastAPI
    app = FastAPI()
    ```

    Next one:

    ```python
    # backend/models.py
    class Todo: ...
    ```

We care about the second-order artefact — turning each fenced block
into a real file on disk. Two signals are checked for the path:

1. A markdown header immediately before the fence: ``### backend/main.py``
   or ``### \`backend/main.py\``` or ``**backend/main.py**``.
2. A first-line comment inside the fence:
   - ``# backend/models.py`` (Python / shell)
   - ``// frontend/src/App.tsx`` (JS / TS)
   - ``<!-- index.html -->`` (HTML / Markdown)

Blocks with neither signal fall back to a synthesised filename based
on the fence language (``file_{n}.{ext}``) so they still land as real
files; users can rename later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(
    r"(?:^|\n)(?P<header>(?:[ \t]*#{1,6}[ \t]+[^\n]*|[ \t]*\*\*[^\n]+\*\*))?[ \t]*\n"
    r"```(?P<lang>[a-zA-Z0-9_+-]*)\n(?P<body>.*?)\n```",
    re.DOTALL,
)

_EXT_BY_LANG = {
    "python": "py",
    "py": "py",
    "javascript": "js",
    "js": "js",
    "typescript": "ts",
    "ts": "ts",
    "tsx": "tsx",
    "jsx": "jsx",
    "html": "html",
    "css": "css",
    "scss": "scss",
    "sass": "sass",
    "bash": "sh",
    "sh": "sh",
    "shell": "sh",
    "yaml": "yml",
    "yml": "yml",
    "json": "json",
    "toml": "toml",
    "markdown": "md",
    "md": "md",
    "dockerfile": "Dockerfile",
    "docker": "Dockerfile",
    "rust": "rs",
    "go": "go",
    "java": "java",
    "kotlin": "kt",
    "swift": "swift",
    "sql": "sql",
    "xml": "xml",
    "yml": "yml",
    "env": "env",
}

_PATH_RE = re.compile(
    r"[a-zA-Z0-9_./\-]+?\.(?:py|ts|tsx|jsx|js|mjs|html?|css|scss|sass|yml|yaml|json|toml|md|sh|bash|rs|go|java|kt|swift|sql|xml|env|Dockerfile|dockerfile)"
)
_DOCKERFILE_RE = re.compile(r"[a-zA-Z0-9_./\-]*Dockerfile(?:\.[a-zA-Z0-9_-]+)?")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


@dataclass(frozen=True)
class ExtractedFile:
    path: str
    content: str
    language: str


def extract_files(markdown: str) -> list[ExtractedFile]:
    """Return every fenced block, paired with a best-effort filepath.

    Ordered as they appear in the source. Paths are relative; callers
    should join them onto the project workspace root before writing.
    """
    out: list[ExtractedFile] = []
    fallback_idx = 0
    for m in _FENCE_RE.finditer(markdown):
        lang = (m.group("lang") or "").strip().lower()
        body = m.group("body")
        header = m.group("header") or ""
        path = _path_from_header(header) or _path_from_body(body, lang)
        if path is None:
            fallback_idx += 1
            ext = _EXT_BY_LANG.get(lang, "txt")
            path = f"untitled_{fallback_idx}.{ext}"
        out.append(ExtractedFile(path=path, content=body, language=lang))
    return out


def _path_from_header(header: str) -> str | None:
    if not header:
        return None
    # Prefer inline-code tokens first: "### `backend/main.py`"
    for inline in _INLINE_CODE_RE.findall(header):
        candidate = inline.strip()
        if _PATH_RE.fullmatch(candidate) or _DOCKERFILE_RE.fullmatch(candidate):
            return candidate
    m = _PATH_RE.search(header)
    if m:
        return m.group(0)
    m2 = _DOCKERFILE_RE.search(header)
    if m2:
        return m2.group(0)
    return None


def _path_from_body(body: str, lang: str) -> str | None:
    first_line = body.splitlines()[0] if body else ""
    stripped = first_line.strip()
    if not stripped:
        return None

    # Strip common comment prefixes then search the rest.
    for prefix in ("# ", "// ", "/* ", "<!-- ", ";; "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    for suffix in (" */", " -->"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]

    m = _PATH_RE.search(stripped)
    if m:
        return m.group(0)
    m2 = _DOCKERFILE_RE.search(stripped)
    if m2:
        return m2.group(0)

    # ``lang`` may itself be "dockerfile" so we take Dockerfile as a
    # whole-filename hit when the body starts with a FROM clause.
    if lang in ("dockerfile", "docker") and body.lstrip().lower().startswith("from "):
        return "Dockerfile"
    return None
