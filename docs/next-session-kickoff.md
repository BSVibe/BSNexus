# Next-Session Kickoff Prompt

> Paste this whole file into the first message of a fresh Claude
> session (or read it as the agent itself) to pick up after the
> `feat/founder-metaphor` merge and start the next sprint without
> losing context.

---

## You are resuming BSNexus on `main` after a major merge

The founder-metaphor redesign just landed. The product is in a known
state with a documented trust gap, a documented hang risk, and three
sprints of work scoped out. Your job is to read the docs that already
exist, propose the next concrete piece of work to the founder, and
execute once they confirm.

**Do NOT** start coding before:
1. You have read the four required docs below.
2. You have checked the current branch and recent commits.
3. You have surfaced 1–2 concrete proposals + confirmed the choice.

## Required reading (in order, before any action)

These are the source of truth for "what just happened" and "what
the product wants to do next":

1. [`CLAUDE.md`](../CLAUDE.md) — assistant rules (MUST / NEVER lists,
   architecture invariants, retired tables you must not resurrect).
2. [`docs/architecture.md`](./architecture.md) — 10 Mermaid diagrams:
   class / sequence / ER / state machine / SSE event types. Skim,
   don't memorize.
3. [`docs/known-issues/`](./known-issues/) — open bugs under
   long-term watch:
   - `llm-tool-loop-hang.md` (1× observed, backstops + tracer in
     place; doc explains how to capture a stack on recurrence)
   - `worker-output-quality.md` (3 concrete bugs in shipped TODO
     app; root cause = Q2 self-verification escape hatch)
4. [`docs/product-direction/`](./product-direction/) — strategy and
   sprint priorities:
   - `visibility-and-trust.md` — six trust uplifts ranked by ROI.
     This is sprint 1 territory.
   - `git-integration.md` — sprint 2–4 territory (internal git → BYO
     remote → PR templating).
   - `existing-project-integration.md` — v2 / OSS maintainer mode.
     Not in immediate scope.

After reading: `git log --oneline -10` and `git status` so you know
what the working tree looks like *now*, not what these docs
described two days ago.

## State invariants (must hold)

- Replanner is iterative. Three outcomes only: `next_step` / `done`
  / `ask_founder`. Don't reintroduce single-shot planning.
- LLM hang backstops are non-negotiable: `asyncio.wait_for`
  on every `litellm.acompletion` and `EXECUTE_WALL_CLOCK_S` budget
  on every `execute()`. Don't remove either.
- Workspace listing must filter `NOISE_PREFIXES` before any
  truncation. See `backend/src/core/project_workspace.py`.
- Per-tenant integration config + Noop fallback for every
  external service (BSage / BSGateway / BSupervisor). Never make
  one of them globally required.
- Schema retired tables (agents, tasks, phases, goals,
  plan_proposals) stay retired. Use Request / ExecutionRun /
  Deliverable / Decision / CompositionSnapshot.

## Candidate next work (sprint 1 — trust polish)

Pick ONE of these per session. They're independent. Sized for ~1–3
days each. Ranked by founder-experience uplift × low risk:

### A. Strengthen Q2 self-verification (recommended first)

**Why**: highest-ROI fix for the worker-output-quality issue.
Today the worker accepts `node -e "require('./server.js')"` as
"verified" and ships broken frontends as `exit=0`. This is the
single bug most likely to break a founder's first demo.

**Where**:
- `backend/src/core/prompts/templates/worker-shared-policy.yaml` —
  tighten Q2 to require process spawn + interaction (curl / playwright
  / `docker run`). Not just `require()` / `cat` / `ls`.
- Optional defense-in-depth: in `backend/src/core/orchestrator_adapter.py`,
  refuse to mark a phase `done` if the tool log shows no `shell_exec`
  with a process-spawn pattern.

**Tests**:
- Update `backend/tests/test_composer_assembler.py` for prompt
  changes.
- New unit test in `tests/test_orchestrator_adapter.py`: artifact-only
  tool log → run blocked with reason "Q2 verification skipped".

**Done when**:
- New TODO-app E2E (same intent as Trace Test on 2026-04-25) ships
  with `exit=0` only when frontend actually loads + backend serves
  static files.

### B. ETA on running phases

**Why**: low effort, high anxiety reduction. Founder doesn't know
if a phase has 30s or 5m left.

**Where**:
- `backend/src/api/project_events.py` — emit a `phase_eta` event
  alongside `phase_start` based on historical median duration of
  similar `phase_name` keywords.
- `frontend/src/components/chat/GlobalChat.tsx` — render "~3 min"
  on the phase pill.

**Done when**:
- Phase pill shows a decaying ETA. Median accuracy ±50% is fine.

### C. Founder-narration deliverable summary

**Why**: the `output_ref.founder_summary` field already exists but
is optional and worker output today is dev-shaped ("Now I'll update
package.json…"). Make it mandatory + scoped.

**Where**:
- `backend/src/core/prompts/templates/worker-shared-policy.yaml` —
  add a "before reporting done, write a 200-word founder summary
  that says (a) what works, (b) what's left, (c) what I need from you"
  step.
- `frontend/src/components/progress/ProgressView.tsx` — surface the
  founder_summary as the primary deliverable copy.

**Done when**:
- Every shipped deliverable has a non-empty `founder_summary`. UI
  reads founder-friendly, not dev-friendly.

### D. Decisions hardening — block runs that depend on unresolved decisions

**Why**: today a worker can in principle ship a Decision-blocked
run anyway. Tighten the gate.

**Where**:
- `backend/src/core/run_orchestrator.py` — pre-dispatch check.

(Lower priority than A/B/C; pick only if A/B/C are all in flight.)

## How to propose

Open a fresh session by:

1. Read the four docs.
2. `git log --oneline main -10` + `git status` to see what's actually
   on disk.
3. Quote one or two of A/B/C above with concrete file paths and a
   1-paragraph approach.
4. Ask the founder which one to start (default A).
5. After confirmation: invoke `/feature-workflow` (TDD), branch
   off `main`, ship.

## Branch + PR conventions

- Branch off `main`: `feat/<area>-<short-slug>` (e.g.
  `feat/q2-spawn-verification`).
- Commits: Conventional Commits (`feat(prompts):`, `fix(adapter):`,
  …). Never include `Co-Authored-By` lines.
- Tests are mandatory. Backend coverage gate is 75% during this
  iteration (target restore 80%); CI enforces.
- Before push: `cd backend && uv run --project . ruff check src/ &&
  uv run --project . pytest` and `cd frontend && pnpm lint && pnpm
  exec tsc -b && pnpm tokens:verify`.

## Anti-goals (do NOT do these without explicit founder ask)

- Pivot toward Lovable / 5-min-prototype UX. The differentiator is
  the founder-asleep timezone. See `visibility-and-trust.md` anti-goals.
- Expose git as the default UX surface. Power-user opt-in only.
  See `git-integration.md` anti-goals.
- Auto-merge any AI-generated PR. Human approval gate is non-negotiable.
- Remove `asyncio.wait_for` / `EXECUTE_WALL_CLOCK_S` "because we have
  better timeouts now". They're cheap insurance against silent hangs;
  the root cause of the 71-min hang is unconfirmed.
- Reintroduce agent / task / phase / goal tables. They were retired
  deliberately. Use Request / ExecutionRun / Deliverable / Decision.

## When the chosen task is done

1. PR with body referencing the relevant doc (`docs/product-direction/...`).
2. After merge, update the relevant doc if the work changed scope or
   discovered something new (e.g., a new known issue).
3. Trigger `/retrospective` only if the work hit a real "approach
   was wrong, had to pivot" moment. Smooth completions don't need it.
4. If a useful pattern emerged, write a skill at
   `~/.claude/claude-skills/skills/<name>/SKILL.md` (don't commit —
   the host poller handles it).

## If something's broken or unclear

- A doc disagrees with reality → trust reality, fix the doc as part
  of your PR.
- The founder asks for something not on the list → confirm scope,
  capture in `docs/product-direction/` if it's a new direction.
- A bug surfaces that's not in `known-issues/` → add a new file there
  before you fix it (long-term tracking discipline).

---

**End of kickoff.** Now: read the four docs, run the git commands,
and propose your first concrete piece of work.
