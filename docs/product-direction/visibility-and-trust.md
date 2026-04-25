# Visibility & Trust — Product Direction Note

## Why this matters

BSNexus's audience is **founders who don't read code**. Conductor's
audience reads code — its trust mechanism is `git diff` plus a PR
review UI. We can't lean on that. The user must trust the system
some other way, or the product fails the moment a deliverable is
broken (which is currently *most* of the time on local LLMs — see
[worker-output-quality.md](../known-issues/worker-output-quality.md)).

This means **trust infrastructure is the central product investment**,
not a polish item. Without it BSNexus becomes "Conductor without
a code reviewer" — strictly worse for the dev audience, and not
believable for the founder audience either.

## What "visibility" should actually deliver

The first-order question the founder is asking, every time:

> "Did the AI do what it said it did?"

The second-order, after they trust phase 1 worked:

> "Why did it choose this approach?"

The third-order, after they trust phase 1 + 2:

> "What's it doing right now, and when can I check back?"

These map to three product surfaces:

| Question | Surface (current) | Gap |
|---|---|---|
| Did it work? | Progress timeline | Just shows "delivered" — no proof |
| Why this choice? | Inside panel | Has composition snapshot, but raw |
| What now / when back? | Direction (chat) | No ETA, no "expected next" |

## Concrete additions worth investing in

Ordered roughly by ratio of "trust uplift" to "effort". Not all need
to land before merge — flagging them so they don't get lost.

### 1. Live preview URL on every code deliverable (high value, medium effort)

When the worker ships a `code` deliverable (server.js + frontend),
the system should:

- Spawn the artifact in an ephemeral runtime (WebContainer, Docker
  socket, or Cloudflare container).
- Surface a clickable URL on the Progress timeline.
- Founder clicks → sees the actual app working in 2 seconds.

This collapses the trust loop from "I have to trust it" to "I just
clicked and it loaded". Lovable/Vercel use this as a *core* trust
mechanism, not a polish item.

Implementation hook: deliverable `type` already supports `url`. Add
a runtime-spawn pipeline that converts code→URL automatically for
common stacks.

### 2. Strengthened Q2 self-verification (high value, low effort)

Today the worker prompt accepts `node -e "require('./server.js')"`
as Q2 verification. This is import-only and produced this session's
broken frontend (TypeError on first script line, never executed).

Tighten the prompt so Q2 *must* run the artifact end-to-end:

- Backend Express server: spawn + curl + assert 200 on declared route
- Frontend HTML: load via headless browser + assert no console errors
- Dockerfile: actually `docker build` + `docker run` + curl

Defense-in-depth at the orchestrator: refuse to mark a phase `done`
if Q2 didn't include a process spawn (`shell_exec` ran a command
that started a process, not just `require()`/`cat`/`ls`).

### 3. Founder-narration deliverable summary (medium value, medium effort)

When a phase completes, the worker writes a 30-second video or 200-word
plain-language summary aimed at the founder, not the dev:

> "Built the backend with 3 endpoints. You can add todos by sending
> POST /todos with a title. I tested it works — see the URL above.
> Things I didn't do yet: user accounts, the homepage. Decisions
> waiting: do you want PostgreSQL or just keep todos in memory?"

This is what a *human* contractor would deliver. AI contractors
should match.

Implementation hook: there's already a `founder_summary` field in
output_ref. Make it mandatory + scoped to "what works / what's next /
what I need from you".

### 4. ETA on running phases (low value, low effort)

Currently chat shows "Backend Setup · just now" forever until done.
The founder doesn't know if it's 2 minutes left or 30. Add:

- Per-phase historical-median ETA based on `phase_name` keyword
  (Backend Setup → ~4 min based on past runs)
- Live decay: shows "~3 min remaining" that ticks down

Don't promise accuracy — even rough ETAs reduce check-back anxiety.

### 5. Decision provenance trail (medium value, high effort)

When a `Decision` row appears, surface the chain that led to it:

- "Worker hit `pnpm install` failure → Replanner suggested 3 paths
  → No clear winner without your input → Asking you."

Without this, decisions feel arbitrary. With this, founder sees
the AI did its work and is escalating responsibly.

Already partially in `composition_snapshots` — needs UI work.

### 6. Reverse interview (high value, high effort, future)

After a deliverable, worker proactively asks:

> "Want me to walk you through how the auth flow works? I made some
> non-obvious choices around session storage."

This is what a junior dev does in standup. AI contractors should match.

This is far enough out it's a v2 item — flag and forget for now.

## Trust mechanisms that DON'T work (avoid)

- **More logs**: Inside panel already exposes everything. Founder
  doesn't read it. Doubling down here is a dev fix for a non-dev
  audience.
- **More reassuring chat tone**: "All looks good!" is anti-trust the
  moment the deliverable doesn't actually work. Every false success
  message permanently lowers founder belief.
- **Confidence scores from the LLM itself**: Self-reported confidence
  is uncorrelated with actual quality. Don't expose it.

## Order of operations (suggestion)

Pre-merge: nothing required from this list (none blocks merge).

Post-merge sprint 1 (1-2 weeks):
- #2 (Q2 strengthening) — directly addresses worst current bug
- #4 (ETA) — quick win, big anxiety reduction
- #3 (founder-narration summary) — re-uses existing field

Post-merge sprint 2 (2-3 weeks):
- #1 (Live preview URL) — single largest trust uplift, justifies B-niche

v2 / future:
- #5 (Decision provenance UI)
- #6 (Reverse interview)

## Anti-goal

We are NOT trying to become Lovable. The 35-min vs 5-min comparison
is a category error — we own the "founder is sleeping" timezone,
not the "founder is iterating" timezone. Do not add scaffold/template
shortcuts to "compete" with Lovable. They will dilute the autonomy
narrative.
