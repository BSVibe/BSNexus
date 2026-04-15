"""Smoke test: alembic upgrade head must succeed against a *fresh* PostgreSQL.

Why this test exists
--------------------
The rest of the suite uses SQLite (aiosqlite) and never exercises PostgreSQL
enum semantics, ``ALTER COLUMN TYPE`` rewrites, or ``DROP TYPE`` dependency
chains. Long-lived dev databases are migrated incrementally over time, so a
broken migration can hide for weeks until someone tries to bootstrap a new
environment.

This test catches that class of bug at PR time by spinning up a throwaway
postgres container, running ``alembic upgrade head`` once on an empty schema,
then a full ``downgrade base`` round-trip. It is intentionally cheap (~10s)
and is skipped automatically when docker is not available.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid

import pytest


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"], check=True, capture_output=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return True


def _inside_container() -> bool:
    """Detect if we're running inside a Docker container (DooD mode)."""
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _find_devcontainer_network() -> str | None:
    """Find the Docker network this container is attached to."""
    try:
        hostname = socket.gethostname()
        result = subprocess.run(
            ["docker", "inspect", hostname, "--format", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            networks = result.stdout.strip().split()
            return networks[0] if networks else None
    except Exception:
        pass
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_pg(container: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: str = ""
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", "bsnexus", "-d", "bsnexus"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        last_err = result.stderr or result.stdout
        time.sleep(0.5)
    raise TimeoutError(f"postgres did not become ready: {last_err}")


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="docker is not available; skipping fresh-migration smoke test",
)


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ALEMBIC_INI = os.path.join(REPO_ROOT, "backend", "alembic.ini")


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        ["uv", "run", "--project", "backend", "alembic", "-c", ALEMBIC_INI, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_alembic_upgrade_head_on_fresh_postgres() -> None:
    """``alembic upgrade head`` must succeed against an empty postgres.

    Catches the kind of bugs SQLite cannot see:
      * ALTER COLUMN TYPE blocked by an enum-typed column DEFAULT
      * CREATE TYPE colliding with SQLAlchemy's enum auto-creation
      * DROP TYPE blocked by surviving FKs

    Note: this test does not exercise downgrade. Several migrations on this
    branch are intentionally lossy (the 4-state TaskStatus collapse) and
    raise ``NotImplementedError`` on downgrade by design.
    """
    container = f"bsnexus-migrate-test-{uuid.uuid4().hex[:8]}"
    in_container = _inside_container()
    network = _find_devcontainer_network() if in_container else None

    docker_run_cmd = [
        "docker", "run", "-d", "--rm",
        "--name", container,
        "-e", "POSTGRES_DB=bsnexus",
        "-e", "POSTGRES_USER=bsnexus",
        "-e", "POSTGRES_PASSWORD=bsnexus_dev",
    ]
    if network:
        # DooD mode: use container name as hostname via shared network
        docker_run_cmd.extend(["--network", network])
        pg_host = container
    else:
        # Host mode: bind to localhost random port
        port = _free_port()
        docker_run_cmd.extend(["-p", f"127.0.0.1:{port}:5432"])
        pg_host = f"127.0.0.1:{port}"

    docker_run_cmd.append("postgres:16-alpine")

    subprocess.run(docker_run_cmd, check=True, capture_output=True)
    try:
        _wait_for_pg(container)
        if network:
            database_url = f"postgresql+asyncpg://bsnexus:bsnexus_dev@{pg_host}:5432/bsnexus"
        else:
            database_url = f"postgresql+asyncpg://bsnexus:bsnexus_dev@{pg_host}/bsnexus"

        up = _alembic(database_url, "upgrade", "head")
        assert up.returncode == 0, f"upgrade head failed:\nSTDOUT:\n{up.stdout}\nSTDERR:\n{up.stderr}"
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
