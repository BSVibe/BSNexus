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

## Setup (devcontainer-only flow)

The standard pattern is to do everything inside the worktree's
devcontainer (`bsnexus-<worktree>-app-1`). The compose stack only
exposes `app`'s ports to the host; postgres / redis / minio reach the
backend via the bsnexus-network bridge, so the host-side `localhost:154xx`
addresses from the project's main CLAUDE.md don't apply for the live
e2e flow.

1. Start the dev stack (postgres, redis, minio, app):
   ```bash
   PROJ="bsnexus-<your-worktree-slug>"
   docker compose -p "$PROJ" \
     -f .devcontainer/docker-compose.yml \
     -f .devcontainer/docker-compose.override.yml \
     --env-file .devcontainer/.env \
     up -d
   ```

2. Apply migrations + start the backend in the devcontainer:
   ```bash
   APP="${PROJ}-app-1"
   docker exec \
     -e DATABASE_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus" \
     -e REDIS_URL="redis://redis:6379" \
     -e E2E_TEST_TOKEN="dev-token" \
     "$APP" zsh -c "cd /workspace/backend && uv run --project . alembic upgrade head"

   docker exec -d \
     -e DATABASE_URL="postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus" \
     -e REDIS_URL="redis://redis:6379" \
     -e E2E_TEST_TOKEN="dev-token" \
     -e OLLAMA_BASE_URL="http://bsserver:11434" \
     -e VERIFIER_ENABLED="true" \
     -e MCP_INTERNAL_URL="http://localhost:8000" \
     "$APP" zsh -c "cd /workspace && uv run --project backend uvicorn backend.src.main:app --host 0.0.0.0 --port 8000 > /tmp/uvicorn.log 2>&1"
   ```

   `MCP_INTERNAL_URL` MUST point at whatever port the backend is
   actually listening on inside the container (default 8000 in this
   flow). The dispatcher mints a run-scoped MCP token and passes
   `${MCP_INTERNAL_URL}/mcp/http/?token=…` to the LLM adapter; if the
   URL doesn't reach a live server, the MCP session connect fails and
   the LLM falls back to no-tools mode (you'll see
   `direct_llm_mcp_connect_failed` warnings + `tools_in_kwargs: false`
   in `/tmp/uvicorn.log`). Real live-LLM dogfood pass on 2026-05-08
   wasted ~15 minutes on this — the default 18100 inherited from the
   main CLAUDE.md doesn't match the inside-the-container port.

3. Install Playwright deps (first time only) inside the devcontainer.
   `NPM_TOKEN` is needed because BSNexus depends on `@bsvibe/*` private
   packages from GitHub Packages:
   ```bash
   TOKEN=$(grep "_authToken=" ~/.npmrc | head -1 | cut -d= -f2)
   docker exec -e CI=true -e NPM_TOKEN="$TOKEN" "$APP" \
     zsh -c "cd /workspace/frontend && pnpm install && pnpm exec playwright install chromium"
   ```

4. Frontend dev server in the devcontainer (Next.js 15 wants
   `--hostname`, not `--host`):
   ```bash
   docker exec -d \
     -e VITE_API_URL="http://localhost:8000" \
     -e NEXT_PUBLIC_API_URL="http://localhost:8000" \
     "$APP" zsh -c "cd /workspace/frontend && pnpm dev --hostname 0.0.0.0 --port 3000 > /tmp/frontend.log 2>&1"
   ```

5. The spec bootstraps its own Ollama executor config via the API
   (`POST /api/v1/executor-configs` with model and base URL from env).
   Pick a model that supports tool calling on the `ollama_chat/`
   provider and isn't a "thinking" model (per memory
   `ollama-litellm-config` always use the `ollama_chat/` prefix).

   ```bash
   docker exec \
     -e LIVE_LLM=1 \
     -e BACKEND_URL="http://localhost:8000" \
     -e FRONTEND_BASE_URL="http://localhost:3000" \
     -e E2E_TEST_TOKEN="dev-token" \
     -e LIVE_LLM_MODEL="ollama_chat/qwen3-coder:30b" \
     -e OLLAMA_BASE_URL="http://bsserver:11434" \
     "$APP" zsh -c "cd /workspace/frontend && pnpm test:e2e:live-llm"
   ```

## Tuning knobs

- `LIVE_LLM_MODEL` — swap to a stronger model if the smaller one fails
  to emit the fenced JSON block reliably.
- The spec waits up to 180s for the Deliverable to appear and 60s for
  proof_state to land at `verified`. Adjust if your LLM/verifier are
  slow.

## Model compatibility — what we observed (2026-05-08 dogfood)

| Model (Ollama tag) | Tool-call quality | Notes |
|---|---|---|
| `qwen3-coder:30b` | smoke ✅, easy/medium ⚠️ hardware-bound | Smoke scenario reached `proof_state=verified` end-to-end (deliverable `1de609bc...` at 12:51 UTC, exit_code=0). Easy/medium intermittent — long multi-turn runs (10+ rounds) hit Ollama Metal OOM on bsserver (`Insufficient Memory ... kIOGPUCommandBufferCallbackErrorOutOfMemory`), surfaces in logs as `litellm.APIConnectionError: Ollama_chatException — KeyError: 'message'` followed by a downstream `RuntimeError: generator didn't stop after athrow()` from `_mcp_session()` cleanup. Hardware constraint, not code. |
| `qwen3-coder:30b` (multi-turn, pre-fix) | round 1 OK, round 2 crashed | The model returned a tool_call with two JSON objects concatenated in `arguments` (`{"path": "a"}{"path": "b"}`); litellm's `ollama/chat/transformation.py` raised `JSONDecodeError: Extra data`. Fixed in this PR by `_split_concatenated_tool_call_arguments()` in `direct_client.py` (uses `json.JSONDecoder().raw_decode()` to walk the buffer; pinned by `tests/test_direct_llm_tool_call_splitter.py`). |
| `qwen3:14b` | silent — 0 tokens emitted | Reasoning model; Ollama treats it as "thinking mode" by default and the response budget gets eaten by the silent CoT. Per memory `ollama-reasoning-model-think-flag`, litellm doesn't forward `think: false` to ollama. |
| `ministral-3:14b` | tool-call works, but ollama rejects | Ollama returned `{"error":"tool 'file_write' not found"}` — the model emitted a tool call referring to a tool it didn't have in its definitions. May be a tool-name mismatch that needs probing. |
| (probe-only) `ollama_chat/qwen3-coder:30b` single-turn | tool_call populates correctly | Memory `litellm-tool-call-provider-probe`'s 10-line probe confirms tool-call works for one round. |

**End-to-end GREEN evidence (smoke):** deliverable `1de609bc-f373-4576-832f-8732c1ee3496`, `proof_state=verified`, `verifier_type=software_test`, `verification_exit_code=0`, `proof_summary="exit=0"`, `verified_at=2026-05-08 12:51:30 UTC`. This proves the entire chain (prompt → fenced block → parser → auto-enqueue → SubprocessVerifier → state machine transition → SSE event) works against a real LLM running on real Ollama hardware.

Bugs caught and fixed during dogfood (in this PR):

1. **Title leak** — when the LLM reply contained only the fenced verification block (no prose), `_first_sentence` walked into the JSON and produced `title = "bsnexus-verification"`. Fixed by `strip_verification_blocks()` + threading the stripped text into `_derive_title()`. Pinned by `tests/test_run_artifacts_verification_hook.py::test_deliverable_title_does_not_leak_verification_block`.
2. **Concatenated tool_call.arguments crash** — `_split_concatenated_tool_call_arguments()` recovers `{"a":1}{"b":2}` into two independent tool_calls before round 2 is sent back to litellm. Pinned by `tests/test_direct_llm_tool_call_splitter.py`.
3. **MCP_INTERNAL_URL gotcha** — documented (default `localhost:18100` from main `CLAUDE.md` doesn't match the in-container uvicorn port `8000` of this dev flow; mismatch silently degrades to no-tools mode).

Outstanding follow-ups (not blocking PR):

1. **`_mcp_session()` cleanup robustness** — when the inner LLM stream raises (e.g. Ollama OOM mid-stream), the streamablehttp_client TaskGroup re-raises but our generator yields again, producing the misleading `RuntimeError: generator didn't stop after athrow()`. Wrap the body so the actual root-cause exception bubbles up untouched.
2. **Tool-call history fallback** for the verification block — parse `shell_exec(...)` invocations from the run's tool-call log when the LLM forgets the fenced block.
3. **Model probe matrix** in CI — a 2-round tool-loop probe per supported model gates which models the live-llm spec defaults to.

## When this fails

- **`tools_in_kwargs: false` in the backend log** → MCP session connect
  failed. Check `MCP_INTERNAL_URL` matches the uvicorn host:port inside
  the container.
- **No fenced block in the LLM reply** → the prompt change in
  `worker-shared-policy.yaml` regressed, or the model is too small to
  follow the instruction. Check the raw chat reply on the backend logs;
  retry with a stronger model (`LIVE_LLM_MODEL`) and if the same model
  used to comply, the prompt is the regression.
- **Block parsed but verifier never runs** → check
  `verifier_enabled=true` and the `verification:queue` consumer-group
  health.
- **Verifier returns non-zero** → the LLM produced a deliverable that
  doesn't actually pass its own self-verification, or the LLM never
  invoked `file_write` (the run reply shows pseudocode like
  `file_write("...", "...")` instead of real tool calls). Real product
  signal: the `proof_state = verification_failed` correctly distinguishes
  the LLM's *claim* from independent server-side verification.
