"""BSNexus Worker Agent — self-hosted task runner.

Like GitHub Actions self-hosted runners:
  1. Register once: bsnexus-worker register
  2. Run in project dir: cd my-project && bsnexus-worker run

The worker polls the BSNexus server for tasks, executes them via
Claude Code CLI in the current directory, and reports results back.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys

import httpx
import structlog

from worker.config import settings

logger = structlog.get_logger(__name__)


async def register(name: str, server_url: str, project_id: str | None = None) -> None:
    """Register this worker with the BSNexus server."""
    async with httpx.AsyncClient(base_url=server_url, timeout=30) as client:
        payload: dict = {"name": name, "capabilities": ["claude_code"]}
        if project_id:
            payload["labels"] = [f"project:{project_id}"]

        res = await client.post("/api/v1/workers/register", json=payload)
        res.raise_for_status()
        data = res.json()

    token = data["token"]
    worker_id = data["id"]

    # Save to .env in current directory
    env_path = ".env"
    env_vars = {
        "BSNEXUS_WORKER_TOKEN": token,
        "BSNEXUS_WORKER_NAME": name,
        "BSNEXUS_SERVER_URL": server_url,
    }
    if project_id:
        env_vars["BSNEXUS_PROJECT_ID"] = project_id

    _update_env_file(env_path, env_vars)

    print(f"\n  Worker registered successfully!")
    print(f"  ID:     {worker_id}")
    print(f"  Name:   {name}")
    print(f"  Server: {server_url}")
    if project_id:
        print(f"  Project: {project_id}")
    print(f"  Token:  saved to {os.path.abspath(env_path)}")
    print(f"\n  Run: bsnexus-worker run\n")


async def poll_and_execute() -> None:
    """Main loop: heartbeat → poll → execute → report."""
    if not settings.worker_token:
        print("Error: No worker token configured.")
        print("Run: bsnexus-worker register --name 'My Machine' --server http://your-server:8000")
        sys.exit(1)

    claude_cmd = shutil.which("claude")
    if not claude_cmd:
        print("Error: 'claude' CLI not found in PATH.")
        print("Install: npm install -g @anthropic-ai/claude-code")
        sys.exit(1)

    cwd = os.getcwd()
    logger.info(
        "worker_starting",
        name=settings.worker_name,
        server=settings.server_url,
        cwd=cwd,
        project=settings.project_id or "(any)",
    )

    headers = {"X-Worker-Token": settings.worker_token}

    async with httpx.AsyncClient(base_url=settings.server_url, timeout=30) as client:
        while True:
            try:
                # Heartbeat
                await client.post("/api/v1/workers/heartbeat", headers=headers)

                # Poll for tasks
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

                    # Project filter
                    if settings.project_id and project_id != settings.project_id:
                        logger.debug("skipping_task_wrong_project", task_id=task_id, expected=settings.project_id, got=project_id)
                        continue

                    logger.info("task_received", task_id=task_id, title=title)

                    # Execute in current directory (the project repo)
                    result = await _execute_claude(claude_cmd, prompt, cwd)

                    # Report
                    await client.post(
                        "/api/v1/workers/result",
                        headers=headers,
                        json={
                            "task_id": task_id,
                            "success": result["success"],
                            "output_data": {"stdout": result.get("stdout", "")},
                            "error_message": result.get("error"),
                        },
                    )
                    status = "success" if result["success"] else "failed"
                    logger.info("task_completed", task_id=task_id, status=status)

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.error("auth_failed", hint="Invalid token. Re-register: bsnexus-worker register")
                    sys.exit(1)
                logger.error("http_error", status=e.response.status_code)
                await asyncio.sleep(settings.poll_interval_seconds)
            except httpx.ConnectError:
                logger.warning("server_unreachable", url=settings.server_url)
                await asyncio.sleep(settings.poll_interval_seconds * 3)
            except Exception:
                logger.exception("worker_error")
                await asyncio.sleep(settings.poll_interval_seconds)


async def _execute_claude(claude_cmd: str, prompt: str, cwd: str) -> dict:
    """Execute task via Claude Code CLI in the given directory."""
    try:
        cmd = [claude_cmd, "--print"]
        if settings.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()),
            timeout=settings.claude_timeout_seconds,
        )

        return {
            "success": proc.returncode == 0,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "error": stderr.decode(errors="replace") if proc.returncode != 0 else None,
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": f"Timeout after {settings.claude_timeout_seconds}s"}
    except FileNotFoundError:
        return {"success": False, "error": "claude CLI not found"}


def _update_env_file(path: str, updates: dict[str, str]) -> None:
    """Update or create .env file with given key=value pairs."""
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
        print("BSNexus Worker — self-hosted task runner")
        print("")
        print("Usage:")
        print("  bsnexus-worker register  Register this machine as a worker")
        print("  bsnexus-worker run       Start polling for tasks")
        print("")
        print("Register options:")
        print("  --name NAME       Worker display name (default: hostname)")
        print("  --server URL      BSNexus server URL")
        print("  --project ID      Bind to specific project (optional)")
        print("")
        print("The worker runs Claude Code in the current directory.")
        print("cd into your project repo before running.")
        sys.exit(0)

    cmd = args[0]

    if cmd == "register":
        import socket
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
        asyncio.run(poll_and_execute())

    else:
        print(f"Unknown command: {cmd}")
        print("Run: bsnexus-worker --help")
        sys.exit(1)


if __name__ == "__main__":
    main()
