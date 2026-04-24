"""Extract fenced code blocks from a run's output into (path, content).

Strategy: find every fenced block first, then for each one walk a short
distance back through the surrounding prose to recover a file path.
Paths can be in:
- a nearby markdown header  ``### \`backend/main.py\```` / ``### main.py``
- a first-line comment inside the fence  ``# backend/models.py`` /
  ``// frontend/App.tsx`` / ``<!-- index.html -->``
- a preceding prose line that mentions ``\`some/path.ext\``` in
  backticks.

Blocks with no recognizable path get a synthesised ``untitled_N.ext``
filename so they still make it to disk. Dockerfile is handled as a
whole-filename when the fence language is ``dockerfile`` / ``docker``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(
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
    "env": "env",
    "text": "txt",
    "": "txt",
}

_FILE_PATH = r"[A-Za-z0-9_][A-Za-z0-9_./\-]*?\.(?:py|ts|tsx|jsx|js|mjs|html?|css|scss|sass|yml|yaml|json|toml|md|sh|bash|rs|go|java|kt|swift|sql|xml|env)"
_PATH_RE = re.compile(_FILE_PATH)
_DOCKERFILE_RE = re.compile(r"(?:[A-Za-z0-9_./\-]+/)?Dockerfile(?:\.[A-Za-z0-9_-]+)?")
_COMPOSE_RE = re.compile(r"docker-compose(?:\.[a-z0-9]+)?\.ya?ml")


@dataclass(frozen=True)
class ExtractedFile:
    path: str
    content: str
    language: str


_SKIPPABLE_LANGS = {"", "text", "txt", "plain", "plaintext", "diff", "log"}


def extract_files(markdown: str) -> list[ExtractedFile]:
    """Return every fenced block paired with a best-effort filepath.

    Blocks with neither an explicit path nor a runnable language
    (e.g. plain-text tree diagrams) are skipped so they don't pollute
    the workspace. Code blocks without a path get an ``untitled_N.ext``
    fallback so they still land as real files.
    """
    out: list[ExtractedFile] = []
    fallback_idx = 0
    for m in _FENCE_RE.finditer(markdown):
        lang = (m.group("lang") or "").strip().lower()
        body = m.group("body")
        preceding = markdown[: m.start()]
        path = (
            _path_from_preceding(preceding)
            or _path_from_body(body, lang)
            or _path_from_lang(body, lang)
        )
        if path is None:
            if lang in _SKIPPABLE_LANGS:
                continue  # decorative fences (trees, ASCII art)
            fallback_idx += 1
            ext = _EXT_BY_LANG.get(lang, "txt")
            path = f"untitled_{fallback_idx}.{ext}"
        out.append(ExtractedFile(path=path, content=body, language=lang))
    return out


def _path_from_preceding(text: str) -> str | None:
    """Walk back through the text, looking for the CLOSEST path mention.

    Skips the immediately-trailing blank lines (there's usually one
    between a markdown header and its fence), then scans non-empty
    lines until the next blank — that's the current section's header
    block. Stops at the blank so we don't bleed into a previous
    section.
    """
    if not text:
        return None
    lines = text.splitlines()
    i = len(lines) - 1
    # Skip the blank cushion between header and fence.
    while i >= 0 and not lines[i].strip():
        i -= 1
    # Now scan the current header block.
    scanned = 0
    while i >= 0 and scanned < 10:
        stripped = lines[i].strip()
        if not stripped:
            break  # next section begins — stop
        path = _path_from_line(stripped)
        if path:
            return path
        i -= 1
        scanned += 1
    return None


def _path_from_line(line: str) -> str | None:
    # Markdown headers and bold markers often wrap the path.
    # Prefer matches inside backticks first — most explicit.
    for inline in re.findall(r"`([^`\n]+)`", line):
        p = _match_path(inline.strip())
        if p:
            return p
    # Fall back to raw match anywhere on the line, but only accept it
    # if the line looks like a section header or file label; otherwise
    # we might catch a path mention in regular prose.
    if (
        line.lstrip().startswith("#")
        or line.lstrip().startswith("**")
        or line.lstrip().startswith("- ")
        or _looks_like_file_label(line)
    ):
        return _match_path(line)
    return None


def _looks_like_file_label(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) > 120:
        return False
    if stripped.endswith(":"):
        return True
    # "N. filename" or "Filename (description)"
    if re.match(r"^\d+[.\)]\s+", stripped):
        return True
    return False


def _match_path(s: str) -> str | None:
    s = s.strip().strip("`\"'*:.(),— ")
    m = _COMPOSE_RE.search(s)
    if m:
        return m.group(0)
    m = _DOCKERFILE_RE.search(s)
    if m:
        return m.group(0)
    m = _PATH_RE.search(s)
    if m:
        return m.group(0)
    return None


def _path_from_body(body: str, lang: str) -> str | None:
    if not body:
        return None
    first_line = body.splitlines()[0].strip()
    if not first_line:
        return None
    for prefix in ("# ", "// ", "/* ", "<!-- ", ";; ", "-- "):
        if first_line.startswith(prefix):
            first_line = first_line[len(prefix):]
            break
    for suffix in (" */", " -->"):
        if first_line.endswith(suffix):
            first_line = first_line[: -len(suffix)]
    return _match_path(first_line)


def _path_from_lang(body: str, lang: str) -> str | None:
    """Last-resort guess from the language tag alone."""
    low_body = body.lstrip().lower()
    if lang in ("dockerfile", "docker"):
        return "Dockerfile"
    if (lang == "yaml" or lang == "yml") and "services:" in low_body and "image:" in low_body:
        return "docker-compose.yml"
    return None
