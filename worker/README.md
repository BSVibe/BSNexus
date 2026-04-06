# BSNexus Worker

Self-hosted worker agent for BSNexus. Like GitHub Actions self-hosted runners — runs on your machine, executes tasks in your project via Claude Code.

## Quick Install

```bash
curl -fsSL https://nexus.bsvibe.dev/worker/install.sh | bash
```

This installs `bsnexus-worker` CLI to `~/.bsnexus-worker/` and adds it to your PATH.

### Prerequisites

- **Python 3.11+**
- **Claude Code CLI** — `npm install -g @anthropic-ai/claude-code`

## Usage

```bash
# 1. Register (one-time, from any directory)
bsnexus-worker register --server https://nexus.bsvibe.dev

# 2. Run (from your project directory)
cd my-project
bsnexus-worker run
```

### Bind to a specific project

```bash
bsnexus-worker register --server https://nexus.bsvibe.dev --project PROJECT_ID
```

The worker will only accept tasks from that project.

## How It Works

```
BSNexus Server                     Your Machine (project dir)
┌──────────────┐                   ┌────────────────┐
│              │◄── heartbeat ────│                │
│  Task Queue  │◄── poll ─────────│  bsnexus-worker │
│              │─── task ────────►│                │
│              │◄── result ───────│  ┌────────────┐│
└──────────────┘                   │  │claude --print│
                                   │  └────────────┘│
                                   └────────────────┘
```

1. **Register** — Worker registers with server, receives auth token (saved to `.env`)
2. **Poll** — Worker polls `/api/v1/workers/poll` every 5 seconds
3. **Execute** — Runs `claude --print` in the current directory (your project repo)
4. **Report** — Sends stdout/stderr back via `/api/v1/workers/result`

## Configuration

All settings via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `BSNEXUS_SERVER_URL` | `https://nexus.bsvibe.dev` | BSNexus URL (defaults to official SaaS) |
| `BSNEXUS_WORKER_TOKEN` | (required) | Auth token from register |
| `BSNEXUS_WORKER_NAME` | hostname | Display name |
| `BSNEXUS_PROJECT_ID` | (optional) | Only accept tasks from this project |
| `BSNEXUS_POLL_INTERVAL_SECONDS` | `5` | Poll interval |
| `BSNEXUS_CLAUDE_TIMEOUT_SECONDS` | `3600` | Max execution time per task |
| `BSNEXUS_SKIP_PERMISSIONS` | `true` | Skip Claude permission prompts |

## Security

- Worker token is a one-time secret — only shown at registration
- Token is hashed (SHA256) server-side, raw token never stored
- Optional project binding limits task scope
- Worker runs in the current directory only — no arbitrary path execution
