# BSNexus Worker

Self-hosted worker agent for BSNexus. Runs on your machine and executes tasks via Claude Code CLI.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

```bash
# Install Claude Code CLI
npm install -g @anthropic-ai/claude-code

# Verify
claude --version
```

## Quick Start

```bash
cd worker

# 1. Install dependencies
uv sync

# 2. Register this worker with your BSNexus server
uv run bsnexus-worker register --name "My MacBook" --server http://your-bsnexus-server:8000

# 3. Start the worker (polls for tasks)
uv run bsnexus-worker run
```

## How It Works

```
BSNexus Server                          Your Machine
┌─────────────┐                        ┌──────────────┐
│  Task Queue  │◄── poll (HTTP) ───────│  Worker Agent │
│  (Redis)     │─── task ─────────────►│  (this CLI)   │
│              │◄── result ────────────│              │
└─────────────┘                        │  ┌──────────┐│
                                       │  │ Claude   ││
                                       │  │ Code CLI ││
                                       │  └──────────┘│
                                       └──────────────┘
```

1. Worker registers with BSNexus server (one-time, saves token to `.env`)
2. Worker polls `/api/v1/workers/poll` for assigned tasks
3. When a task arrives, executes it via `claude --print` subprocess
4. Reports result back via `/api/v1/workers/result`
5. Sends periodic heartbeat to stay online

## Configuration

Environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `BSNEXUS_SERVER_URL` | `http://localhost:8000` | BSNexus server URL |
| `BSNEXUS_WORKER_TOKEN` | (required) | Worker auth token (from register) |
| `BSNEXUS_WORKER_NAME` | | Worker display name |
| `BSNEXUS_WORKSPACE_DIR` | `.` | Working directory for Claude Code |
| `BSNEXUS_POLL_INTERVAL_SECONDS` | `5` | Polling interval |
| `BSNEXUS_CLAUDE_TIMEOUT_SECONDS` | `3600` | Max execution time per task |
| `BSNEXUS_SKIP_PERMISSIONS` | `true` | Skip Claude Code permission prompts |
