# LLM Tool-Loop Hang (under long-term watch)

## Status

Active long-term watch. Defended by backstops, root cause not confirmed.
First observed 2026-04-25 in worktree `feat-founder-metaphor`. Not
reproduced in the immediate retest — likely non-deterministic.

## Symptom

A run sits in `running` state indefinitely (observed: 71+ minutes)
with no further progress. From outside the process:

- Backend FastAPI worker is alive, CPU 0%, HTTP listener still serving.
- `lsof` shows zero ESTABLISHED outbound connections to the LLM
  provider (Ollama on `localhost:11434` in this case).
- No shell-exec child processes are running.
- No new entries in `execution_run_activities` since the
  `run_started` milestone.

The litellm `timeout=600` kwarg does not fire — the coroutine simply
never wakes.

## What we know about location

Captured from the Ollama HTTP access log around the hang window:

```
16:57:39 200 13.586s POST /api/chat   ← iteration 1 returned cleanly
(no /api/chat for this client until manual stop 71+ min later)
```

So iteration 1 of `LiteLLMOrchestratorAdapter.execute()` got a valid
response. Iteration 2 never issued the next `/api/chat` request.

The hang is between iteration 1's response handling and iteration 2's
LLM call — i.e. inside the `for call in tool_calls: ...` block, or
between iterations in the outer loop. None of our tool handlers do a
real network/IPC await except `shell_exec`, which has its own
`asyncio.wait_for(timeout=180s)` — and no shell child was alive
during the hang.

## Hypotheses (unconfirmed)

1. **httpx connection-close race.** The Ollama → litellm → httpx call
   chain may have lost the connection-closed signal silently after
   iteration 1, leaving an internal AnyIO/httpx future awaiting a frame
   that never arrives. CPU 0% and zero outbound sockets are consistent
   with this.
2. **litellm internal retry/backoff loop.** Some litellm versions do
   internal retries on certain Ollama responses without honoring the
   user `timeout` kwarg, parking on an `asyncio.sleep` chain. Less
   likely given CPU 0% across 71 minutes.
3. **Race between tool-call execution and the assistant message
   `messages.append`.** Almost certainly not — that path has no
   awaits — but worth noting as a remaining possibility if the
   hypotheses above are ruled out.

## What the next reproduction should capture

When this happens again:

1. Send `kill -USR2 <worker_pid>` — dumps every asyncio task's stack
   to `/tmp/bsnexus-trace.log` (path overridable via
   `BSNEXUS_TRACE_DUMP_PATH`).
2. Send `kill -USR1 <worker_pid>` — dumps every OS-thread's Python
   stack to the same file.
3. Find the dispatch task (`_dispatch_background()` frame). Its
   inner-most await tells us *exactly* which line is parked.
4. Cross-reference with backend stderr — every iteration entry/exit
   and every tool entry/exit emits a structured log
   (`llm_iteration_start` / `llm_iteration_returned` /
   `tool_call_start` / `tool_call_done`). The last `tool_call_done`
   without a matching next `llm_iteration_start` brackets the hang to
   one branch.

## Backstops in place

These limit blast radius even without a root-cause fix:

- `backend/src/core/orchestrator_adapter.py`:
  - `asyncio.wait_for(litellm.acompletion(...), timeout=REQUEST_TIMEOUT * 1.2)`
    on every LLM call. Fires `TimeoutError` even when litellm's own
    `timeout` is ignored. (`LLM_REQUEST_TIMEOUT` default 600s ⇒ outer 720s.)
  - `EXECUTE_WALL_CLOCK_S` (default 1800s = 30 min) on the entire
    tool loop. Iteration n+1 checks the deadline before issuing the
    next LLM call; on exhaustion sets `stop_reason="wall_clock_exhausted"`
    and breaks cleanly. Without this, 24 iterations × 600s = 4 h
    worst-case.
- `_dispatch_background` already wraps `adapter.execute()` in
  `try/except` and on failure marks the run `blocked` with the
  exception message. So a `TimeoutError` propagates to a clean
  state transition instead of leaving the run stuck.

## Tracer setup (always loaded)

- `_install_thread_traceback_signal()` registers `faulthandler` on
  `SIGUSR1`, writing thread tracebacks to the dump file.
- `_install_asyncio_signal()` registers a `loop.add_signal_handler`
  on `SIGUSR2`, walking `asyncio.all_tasks()` and printing each task's
  stack.
- Per-iteration / per-tool structlog INFO events bracket the hot path.

## Out-of-scope here

- The replanner / decomposition logic is fine; this is purely an
  in-process await issue.
- Worker prompt quality (e.g. interpreting "deployment" as README
  rather than a deploy manifest) is tracked separately.

## When to revisit

Revisit if:

1. The hang reproduces with the tracer attached and we get a stack
   pinning the await location.
2. We migrate off `litellm` for Ollama (e.g. direct ollama-python
   client) and the symptom disappears — confirms hypothesis #2.
3. We see the same symptom against a non-Ollama backend (Anthropic,
   OpenAI) — would invalidate the Ollama-specific theory.
