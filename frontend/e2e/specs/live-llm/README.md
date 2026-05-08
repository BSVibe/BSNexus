# Live-LLM end-to-end specs

Specs in this folder hit a **real backend with a real LLM**. They are
excluded from the default `pnpm test:e2e` run (the `playwright.config.ts`
testIgnore filters `**/live-llm/**` unless `LIVE_LLM=1` is set) so PR CI
stays deterministic.

## What they prove

- The worker emits a `bsnexus-verification` fenced JSON block at the end
  of its chat reply (decision-locks A1, prompt update PR6).
- `_ensure_deliverable` parses that block, stamps the Deliverable, and
  the auto-enqueue hook in `publish_run_output` pushes an envelope onto
  the verifier queue.
- The Verifier Worker dequeues, runs the `SubprocessVerifier`, and
  transitions the Deliverable to `proof_state = verified`.
- The Brief surface receives the `deliverable_proof` SSE event and
  shows the ProofBadge in its terminal state.

These are the only tests that catch a regression in the **live LLM
behaviour** — i.e. "did the prompt change make the worker stop emitting
the fenced block?" — that mock-API tests cannot.

## Setup

1. Start the BSNexus dev stack (Postgres, Redis, MinIO):
   ```bash
   docker compose -p bsnexus-founder \
     -f .devcontainer/docker-compose.yml \
     -f .devcontainer/docker-compose.override.yml \
     --env-file .devcontainer/.env \
     up -d postgres redis minio
   ```

2. Run the backend with `E2E_TEST_TOKEN` and the verifier enabled
   (default). The Ollama endpoint is the BSVibe Tailscale host
   `bsserver:11434` so the devcontainer can reach it without DNS gymnastics:
   ```bash
   DATABASE_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@localhost:15434/bsnexus" \
   REDIS_URL="redis://localhost:16381" \
   E2E_TEST_TOKEN="dev-token" \
   OLLAMA_BASE_URL="http://bsserver:11434" \
   uv run --project backend alembic upgrade head
   uv run --project backend uvicorn backend.src.main:app --host 0.0.0.0 --port 18100 --app-dir . --reload
   ```

3. Start the frontend dev server pointed at this backend:
   ```bash
   cd frontend
   VITE_API_URL=http://localhost:18100 \
   VITE_AUTH_URL=https://auth.bsvibe.dev \
   pnpm dev --host 0.0.0.0 --port 13100
   ```

4. The test bootstraps its own Ollama executor config via the API
   (`POST /api/v1/executor-configs` with model
   `ollama_chat/qwen3-coder:30b`, base URL `http://bsserver:11434`).
   That model lives on the Tailscale Ollama host. If you swap models,
   keep the `ollama_chat/` provider prefix — `ollama/` is fine for
   trivial 1-prompt calls but breaks on tool-call / multi-turn /
   JSON-output flows ([memory: ollama-litellm-config]).

5. Run the live spec:
   ```bash
   FRONTEND_BASE_URL=http://localhost:13100 \
   BACKEND_URL=http://localhost:18100 \
   E2E_TEST_TOKEN=dev-token \
   pnpm test:e2e:live-llm
   ```

## Tuning knobs

- `LIVE_LLM_MODEL` (default `ollama_chat/qwen3-coder:30b`) — swap to a
  bigger model if the small one fails to emit the fenced JSON block
  reliably.
- The spec waits up to 180s for the Deliverable to appear and 60s for
  proof_state to land at `verified`. Adjust if your LLM/verifier are
  slow.

## When this fails

- **No fenced block in the LLM reply** → the prompt change in
  `worker-shared-policy.yaml` regressed, or the model is too small to
  follow the instruction. Check the raw chat reply on the backend logs
  / Inside surface; retry with a stronger model (`LIVE_LLM_MODEL`) and
  if the same model used to comply, the prompt is the regression.
- **Block parsed but verifier never runs** → check
  `verifier_enabled=true` and the `verification:queue` consumer-group
  health.
- **Verifier returns non-zero** → the LLM produced a deliverable that
  doesn't actually pass its own self-verification. Real product bug;
  open a follow-up.
