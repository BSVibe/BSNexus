"""BSNexus Worker Agent — self-hosted task runner.

Runs on your machine, polls for tasks, executes via pluggable CLI tools
(Claude Code, Codex, OpenCode, etc.), reports results back.

Usage:
    bsnexus-worker register --name "My Machine"
    cd my-project && bsnexus-worker run
    bsnexus-worker run --executor codex
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys

import httpx
import structlog

from worker.config import settings
from worker.executors import CLIExecutor, detect_available, get_executor

logger = structlog.get_logger(__name__)


async def register(name: str, server_url: str, project_id: str | None = None) -> None:
    """Register this worker with the BSNexus server."""
    # Detect available executors on this machine
    available = detect_available()
    capabilities = available if available else ["claude_code"]

    async with httpx.AsyncClient(base_url=server_url, timeout=30) as client:
        payload: dict = {"name": name, "capabilities": capabilities}
        if project_id:
            payload["labels"] = [f"project:{project_id}"]

        res = await client.post("/api/v1/workers/register", json=payload)
        res.raise_for_status()
        data = res.json()

    token = data["token"]
    worker_id = data["id"]

    env_vars = {
        "BSNEXUS_WORKER_TOKEN": token,
        "BSNEXUS_WORKER_NAME": name,
        "BSNEXUS_SERVER_URL": server_url,
    }
    if project_id:
        env_vars["BSNEXUS_PROJECT_ID"] = project_id

    _update_env_file(".env", env_vars)

    print(f"\n  Worker registered!")
    print(f"  ID:           {worker_id}")
    print(f"  Name:         {name}")
    print(f"  Server:       {server_url}")
    print(f"  Capabilities: {', '.join(capabilities)}")
    if project_id:
        print(f"  Project:      {project_id}")
    print(f"  Token:        saved to .env")
    print(f"\n  Run: cd your-project && bsnexus-worker run\n")


async def poll_and_execute(executor_name: str) -> None:
    """Main loop: heartbeat → poll → execute → report."""
    if not settings.worker_token:
        print("Error: No worker token. Run 'bsnexus-worker register' first.")
        sys.exit(1)

    try:
        executor = get_executor(
            executor_name,
            timeout=settings.claude_timeout_seconds,
            skip_permissions=settings.skip_permissions,
        )
    except KeyError as e:
        print(f"Error: {e}")
        sys.exit(1)

    cmd_path = executor.resolve_cmd()
    if not cmd_path:
        print(f"Error: '{executor.cli_command}' not found.")
        print(f"Install: {executor.install_hint}")
        sys.exit(1)

    cwd = os.getcwd()
    logger.info(
        "worker_starting",
        name=settings.worker_name,
        executor=executor.name,
        cli=cmd_path,
        cwd=cwd,
        project=settings.project_id or "(any)",
    )

    headers = {"X-Worker-Token": settings.worker_token}

    async with httpx.AsyncClient(base_url=settings.server_url, timeout=30) as client:
        while True:
            try:
                await client.post("/api/v1/workers/heartbeat", headers=headers)

                res = await client.post("/api/v1/workers/poll", headers=headers, params={"count": 1})
                res.raise_for_status()
                tasks = res.json()

                if not tasks:
                    await asyncio.sleep(settings.poll_interval_seconds)
                    continue

                for task in tasks:
                    task_id = task["task_id"]
                    project_id = task.get("project_id", "")
                    title = task.get("title", "")
                    prompt = task.get("prompt") or title

                    if settings.project_id and project_id != settings.project_id:
                        logger.debug("skipping_wrong_project", task_id=task_id)
                        continue

                    logger.info("task_received", task_id=task_id, title=title)

                    result = await executor.execute(prompt, cwd)

                    await client.post(
                        "/api/v1/workers/result",
                        headers=headers,
                        json={
                            "task_id": task_id,
                            "success": result.success,
                            "output_data": {"stdout": result.stdout},
                            "error_message": result.error,
                        },
                    )
                    logger.info("task_completed", task_id=task_id, success=result.success)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.error("auth_failed", hint="Invalid token. Re-register.")
                    sys.exit(1)
                logger.error("http_error", status=e.response.status_code)
                await asyncio.sleep(settings.poll_interval_seconds)
            except httpx.ConnectError:
                logger.warning("server_unreachable", url=settings.server_url)
                await asyncio.sleep(settings.poll_interval_seconds * 3)
            except Exception:
                logger.exception("worker_error")
                await asyncio.sleep(settings.poll_interval_seconds)


def _update_env_file(path: str, updates: dict[str, str]) -> None:
    lines: list[str] = []
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        pass

    existing = set()
    new_lines = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            existing.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in existing:
            new_lines.append(f"{key}={value}\n")
    with open(path, "w") as f:
        f.writelines(new_lines)


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        available = detect_available()
        print("BSNexus Worker — self-hosted task runner")
        print("")
        print("Usage:")
        print("  bsnexus-worker register  Register this machine")
        print("  bsnexus-worker run       Start polling for tasks")
        print("")
        print("Options:")
        print("  --name NAME       Worker name (default: hostname)")
        print("  --server URL      BSNexus URL (default: nexus.bsvibe.dev)")
        print("  --project ID      Bind to project")
        print("  --executor NAME   CLI executor (default: auto-detect)")
        print("")
        print(f"  Available executors on this machine: {', '.join(available) if available else '(none found)'}")
        print("  Supported: claude_code, codex, opencode")
        sys.exit(0)

    cmd = args[0]

    if cmd == "register":
        name = socket.gethostname()
        server = settings.server_url
        project_id = None
        i = 1
        while i < len(args):
            if args[i] == "--name" and i + 1 < len(args):
                name = args[i + 1]; i += 2
            elif args[i] == "--server" and i + 1 < len(args):
                server = args[i + 1]; i += 2
            elif args[i] == "--project" and i + 1 < len(args):
                project_id = args[i + 1]; i += 2
            else:
                i += 1
        asyncio.run(register(name, server, project_id))

    elif cmd == "run":
        executor_name = ""
        i = 1
        while i < len(args):
            if args[i] == "--executor" and i + 1 < len(args):
                executor_name = args[i + 1]; i += 2
            else:
                i += 1

        # Auto-detect executor if not specified
        if not executor_name:
            available = detect_available()
            if not available:
                print("Error: No supported CLI executor found.")
                print("Install one of: claude (npm i -g @anthropic-ai/claude-code), codex (npm i -g @openai/codex), opencode")
                sys.exit(1)
            executor_name = available[0]
            if len(available) > 1:
                logger.info("auto_detected_executor", selected=executor_name, available=available, hint="override with --executor")
            else:
                logger.info("auto_detected_executor", selected=executor_name)

        asyncio.run(poll_and_execute(executor_name))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
