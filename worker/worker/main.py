"""BSNexus Worker Agent — polls for tasks and executes them locally.

Usage:
    # Register first (one-time)
    bsnexus-worker register --name "My MacBook" --server http://bsnexus.example.com

    # Then run
    bsnexus-worker run
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time

import httpx
import structlog

from worker.config import settings

logger = structlog.get_logger(__name__)


async def register(name: str, server_url: str) -> None:
    """Register this worker with the BSNexus server."""
    async with httpx.AsyncClient(base_url=server_url, timeout=30) as client:
        res = await client.post(
            "/api/v1/workers/register",
            json={"name": name, "capabilities": ["claude_code"]},
        )
        res.raise_for_status()
        data = res.json()

    token = data["token"]
    worker_id = data["id"]

    # Save to .env
    env_path = ".env"
    lines = []
    try:
        with open(env_path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        pass

    # Update or append
    updated = {"BSNEXUS_WORKER_TOKEN": token, "BSNEXUS_WORKER_NAME": name, "BSNEXUS_SERVER_URL": server_url}
    existing_keys = set()
    new_lines = []
    for line in lines:
        key = line.split("=")[0].strip()
        if key in updated:
            new_lines.append(f"{key}={updated[key]}\n")
            existing_keys.add(key)
        else:
            new_lines.append(line)
    for key, value in updated.items():
        if key not in existing_keys:
            new_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)

    print(f"Worker registered: {name} (id: {worker_id})")
    print(f"Token saved to {env_path}")
    print(f"Run: bsnexus-worker run")


async def poll_and_execute() -> None:
    """Main loop: poll for tasks, execute via claude CLI, report results."""
    if not settings.worker_token:
        print("Error: No worker token. Run 'bsnexus-worker register' first.")
        sys.exit(1)

    claude_cmd = shutil.which("claude")
    if not claude_cmd:
        print("Error: 'claude' CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        sys.exit(1)

    logger.info("worker_starting", name=settings.worker_name, server=settings.server_url)

    headers = {"X-Worker-Token": settings.worker_token}

    async with httpx.AsyncClient(base_url=settings.server_url, timeout=30) as client:
        while True:
            try:
                # Heartbeat + poll
                await client.post("/api/v1/workers/heartbeat", headers=headers)

                res = await client.post(
                    "/api/v1/workers/poll",
                    headers=headers,
                    params={"count": 1},
                )
                res.raise_for_status()
                tasks = res.json()

                if not tasks:
                    await asyncio.sleep(settings.poll_interval_seconds)
                    continue

                for task in tasks:
                    task_id = task["task_id"]
                    title = task.get("title", "unknown")
                    prompt = task.get("prompt", title)
                    logger.info("task_received", task_id=task_id, title=title)

                    # Execute via claude CLI
                    result = await execute_claude(claude_cmd, prompt)

                    # Report result
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
                    logger.info("task_completed", task_id=task_id, success=result["success"])

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    logger.error("auth_failed", detail="Invalid worker token. Re-register.")
                    sys.exit(1)
                logger.error("http_error", status=e.response.status_code)
                await asyncio.sleep(settings.poll_interval_seconds)
            except httpx.ConnectError:
                logger.warning("server_unreachable", url=settings.server_url)
                await asyncio.sleep(settings.poll_interval_seconds * 2)
            except Exception:
                logger.exception("worker_error")
                await asyncio.sleep(settings.poll_interval_seconds)


async def execute_claude(claude_cmd: str, prompt: str) -> dict:
    """Execute a task via Claude Code CLI."""
    try:
        cmd = [claude_cmd, "--print"]
        if settings.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=settings.workspace_dir,
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


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  bsnexus-worker register --name 'My Machine' --server http://localhost:8000")
        print("  bsnexus-worker run")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "register":
        name = "Worker"
        server = settings.server_url
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--name" and i + 1 < len(args):
                name = args[i + 1]
                i += 2
            elif args[i] == "--server" and i + 1 < len(args):
                server = args[i + 1]
                i += 2
            else:
                i += 1
        asyncio.run(register(name, server))

    elif cmd == "run":
        asyncio.run(poll_and_execute())

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
