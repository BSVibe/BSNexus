# Worker Output Quality (under long-term watch)

## Status

Active long-term watch. Documented from end-to-end run on
2026-04-25 (project "Trace Test", model `ollama_chat/qwen3-coder:30b`).
The replanner / orchestration layer worked correctly — these are
artifact-level quality bugs the worker LLM produced inside otherwise
"successful" phases. No code changes yet; tracked here for prompt /
verification hardening once we have clearer signal.

## What got built

End-to-end test ran the founder intent
`간단한 todo 웹앱을 만들어줘. 백엔드 만들고, 프론트엔드 만들고, 배포까지 진행해줘.`

7 replanner phases ran to clean `chain_done`. Final artifacts:

```
package.json, server.js                       (backend)
index.html, style.css, script.js              (frontend)
Dockerfile, heroku.yml                        (deployment)
server-test.js, test-frontend.html            (tests)
```

Replanner reported "모든 요구사항이 완료되었습니다…모두 구현했습니다."
The artifacts demo as "looks finished" but **don't actually work
end-to-end**:

| Surface         | Status                                  |
|-----------------|-----------------------------------------|
| REST API        | ✅ All 4 endpoints work, 400/404 OK     |
| Frontend UI     | ❌ JS exception on first script line    |
| Same-origin     | ❌ Server doesn't serve the frontend    |
| Heroku deploy   | ❌ PORT env ignored, will crash on boot |

## The three concrete bugs

### Bug 1 — `server.js` hardcodes port 3000, ignores `process.env.PORT`

```javascript
const port = 3000;  // production ignores PORT env, Heroku boot fails
```

Phase 5 directive explicitly said
`Make sure the server listens on the correct port (process.env.PORT)`.
The worker still shipped the hardcoded version. Phase verification
returned `exit=0` regardless.

### Bug 2 — `server.js` has no static-file middleware

```javascript
// missing: app.use(express.static(__dirname));
```

Effect: `GET /` returns Express's default `Cannot GET /` 404 HTML.
The frontend (`index.html`, `script.js`, `style.css`) is in the same
directory as `server.js` but unreachable via the same origin. A
browser opening `http://localhost:3000/` sees a broken app.

The replanner's deployment phase output called for "the backend
serving the static frontend files" — the directive was right, the
worker just didn't add the middleware.

### Bug 3 — `script.js` references `#todoForm` that doesn't exist in `index.html`

```javascript
const todoForm = document.getElementById('todoForm');  // null
todoForm.addEventListener('submit', ...);              // TypeError
```

Effect: TypeError on first non-trivial line of `script.js`. The
exception aborts the entire script — no `DOMContentLoaded` listener
gets registered, no Add-button handler, no `fetchTodos()` call. The
frontend is **completely dead** even when served correctly.

Browser console (verified via Playwright):

```
TypeError: Cannot read properties of null (reading 'addEventListener')
  at script.js:10:10
```

`index.html` has `<input id="todoInput">` and `<button id="addBtn">` —
no `<form id="todoForm">`. Frontend ↔ backend cross-reference (DOM
IDs vs `getElementById` calls) was never validated by the worker.

## Why the worker reported success

The worker's system prompt mandates:

> Self-verification — MANDATORY before you report a phase done:
>   Q1. Success condition in observable terms
>   Q2. Verification command via shell_exec that proves Q1
>   Q3. Loop until Q2 passes or 3 attempts

In practice the worker is interpreting "Q2 verification command" as
something like `node -e "require('./server.js')"` (import-only). That
passes with exit 0 even though the artifact is broken at runtime. The
prompt's escape hatch is the `compile / test / validate / start-and-curl`
list — `node -e "require()"` arguably falls under "validate" without
actually running the artifact.

This is a prompt-side issue, not a worker-LLM-quality issue per se.
Defense ideas (not yet implemented):

- Tighten Q2 to require a *runtime exercise*: spawn the server, hit
  `/todos`, assert 200; or load the HTML via puppeteer/curl and assert
  no console errors.
- Add a separate cross-check phase: "given the backend defines path X
  with field Y, confirm the frontend's fetch() call uses the same X
  and reads Y."
- Add a deploy preflight: grep for `process.env.PORT` when the
  directive mentions Heroku / containers / cloud.

## Hypothesis: stronger models help, but escape hatches still apply

`qwen3-coder:30b` (local Ollama) misses these patterns. Frontier models
(Claude Sonnet 4.6 / GPT-5-class) would near-certainly catch:

- **Bug 1** — `process.env.PORT || 3000` is the default Node idiom,
  especially with Dockerfile / heroku.yml in context.
- **Bug 2** — when prompts explicitly say "backend serves static
  frontend files", static middleware is a default reflex.
- **Bug 3** — DOM-ID ↔ `getElementById` cross-reference is high-density
  in training data; rarely missed.

But the **Q2 import-only escape hatch is prompt-level** — frontier
models can use it too if the prompt allows it. So model upgrade
helps ~80% of these but not the verification hole.

## How to A/B test (when ready)

The codebase already has BSGateway integration (LiteLLM hook). Switch
the active `executor_configs` row for the test tenant from
`ollama_chat/qwen3-coder:30b` to a Sonnet/GPT-class model via
BSGateway, re-run the same intent, compare artifact quality. No code
changes required — it's a tenant-level config flip.

## When to revisit

Revisit when one of these is true:

1. Self-verification prompt gets tightened (worker output for
   single-phase build-and-curl drops to <30s without import-only
   shortcuts).
2. We start using BSGateway for the worker LLM (frontier model) —
   rerun this same scenario and re-assess which bugs survive.
3. We add a deployment-platform integration that includes a real
   preflight check (grep for hardcoded port, missing static, etc.).

## Out-of-scope here

- Replanner / orchestration logic — works correctly.
- The LLM hang issue — tracked in
  [llm-tool-loop-hang.md](./llm-tool-loop-hang.md).
- The "phase_name keeps repeating" UX — minor, noted but not a
  quality bug.
