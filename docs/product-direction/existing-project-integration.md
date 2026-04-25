# Existing Project Integration — Product Direction Note

## Why this matters

With three pieces in place — git/PR integration, replanner, and trust
mechanisms — a fourth use case falls out of the architecture almost
for free: **point BSNexus at an existing repo and have it act as a
maintainer**. Issues become Requests. Replanner triages. Worker
opens PRs. Auto-review pass + CI check + preview URL provide trust.

This is a natural emergent capability, not a pivot. The same mental
model ("hire an AI company to do work for you") applies — just to a
different shape of work.

## What unlocks

Concrete capabilities the current architecture already wants to support:

### 1. Issue auto-fix with PR

```
GitHub issue #1234 ──► BSNexus Request (intent_summary = issue title+body)
                  ──► Replanner: "reproduce → fix → test → PR"
                  ──► Worker: clones, branches, edits, runs tests
                  ──► Auto-review pass (separate LLM with diff context)
                  ──► PR opened against repo with founder summary
                  ──► Maintainer reviews on GitHub OR via BSNexus Decisions
```

This is the Sweep / Codegen / Cursor-background-agent space. None
own it; market is open.

### 2. PR review pass for human PRs

Inverse direction: human opens PR, BSNexus reviews it.

```
Human PR opened ──► Webhook to BSNexus ──► Request (intent: "review PR")
                ──► Replanner picks "review pass": [diff analysis,
                    test impact, security check, suggestions]
                ──► Comments posted on the PR via GitHub API
                ──► Maintainer sees AI review + their human review
```

Pairs naturally with #1: AI maintainer fixes its own bugs, then
reviews humans' contributions to keep noise out of maintainer inbox.

### 3. Periodic codebase health passes

Replanner can be cron-triggered, not just user-triggered:

- Weekly: "scan for outdated deps and propose upgrade PR"
- Monthly: "scan for missing test coverage on hot paths"
- On-demand: "audit security implications of last 30 days of merges"

Decisions surface for "this upgrade has breaking changes — which
strategy?" — exactly what BSNexus is designed for.

### 4. Maintainer dashboard view

A repo-scoped variant of the Direction/Progress/Decisions surface:

- Direction: maintainer chats issues to AI ("triage all 'bug' issues from this week")
- Progress: deliverable timeline (this PR, that PR, this audit)
- Decisions: "approve this dep upgrade?", "is this issue a duplicate?"
- Inside: per-PR composition snapshot (why the AI chose this fix)

Same UI primitives, repo-aware data binding.

## What changes for the dev audience

Founder mode and maintainer mode use the same backend, but the
**trust model is different**:

| | Founder mode | Maintainer mode |
|---|---|---|
| Audience | Doesn't read code | **Does read code** |
| Trust source | Live preview, Q2 result, deliverable summary | Git diff + test result + review comments |
| Escape hatch | "forward PR to dev" | They ARE the dev |
| Failure cost | "demo broke" | "AI merged garbage to my OSS project" |

The failure cost asymmetry is important: a maintainer's reputation
is on the line every time AI pushes to their repo. So:

- **Auto-merge is opt-in only.** Default = open PR, request human merge.
- **Trust signals must be machine-verifiable** (test exit codes,
  type-check, lint clean), not LLM self-reports.
- **Strict scope**: AI fixes the issue it was assigned, doesn't
  touch unrelated files. Replanner needs to enforce this constraint.
- **Honest limits**: AI says "I couldn't fix this" out loud rather
  than guessing. False positives erode trust faster in dev audience
  than in founder audience.

## What's needed (technical work beyond git+import)

Beyond what git/PR integration already buys, this use case needs:

### 1. Codebase comprehension primitive

A scratch project starts empty; an existing repo has 10k+ files. The
worker prompt assumes empty workspace baseline. Need:

- **Repo-shape index**: file tree, language stats, top-level structure,
  framework detection (`package.json` / `pyproject.toml` /
  `Cargo.toml` etc.). Cached per repo.
- **Convention extraction**: existing test framework, lint config,
  commit message style, PR template. Worker should match.
- **Hot-path map**: most-edited files in last 90 days, key entry
  points. Replanner uses this to scope phase directives.
- **Semantic search**: "where is auth handled?" → file:line. Could
  be BSage's job (search/retrieve is its boundary), tying back to
  existing ecosystem.

This isn't a tiny addition — it's a real subsystem. ~1-2 sprints.

### 2. Issue ingestion + triage

GitHub webhook → Request. Plus:

- **Triage classifier**: which issues are auto-fixable vs need human?
  False positive rate must stay near zero. Easier issues:
  typos, dep upgrades, missing test cases, simple bugs with reproducer.
  Harder: feature requests, architecture decisions, ambiguous bugs.
- **Reproducer step**: before any fix, worker tries to reproduce the
  bug (run tests, check minimal repro). If can't reproduce → reply
  on issue, "I need more info: [questions]". Don't waste cycles
  fixing what isn't broken.
- **Issue dedup**: don't open 5 PRs for the same root-cause issue.

### 3. PR review pass (separate from worker)

A different LLM call with a different system prompt:

- Input: PR diff, base branch context, related code, test results.
- Output: review comments, approve/request-changes, blocker list.
- Persona: "senior reviewer who hasn't seen this code before".

Distinct from the worker because the worker has built-up context;
the reviewer should be fresh-eyes.

### 4. Permission + abuse surface

Push access to a maintainer's OSS repo is a credential. Need:

- Scoped PATs (per-repo, branch-write only, no force-push, no admin).
- Audit log: every API call, every commit, every PR.
- Abuse circuit-breakers: "10 PRs in 1 minute" → throttle.
- Webhook signature verification.

This is real engineering work. It's the price of operating in someone
else's repo.

### 5. CI integration

Trust mechanism upgrade: instead of BSNexus's internal Q2
verification, use the repo's CI as the source of truth.

- Worker pushes commits, watches CI run, doesn't open PR until green.
- If CI red after 3 fix attempts → mark Request blocked + Decision
  "I can't get CI green — here's what's failing, please advise."
- For OSS repos with no CI: spin up ephemeral CI (GitHub Actions
  one-shot) using workflow file we synthesize.

CI as Q2 is third-party-verifiable in a way our internal `shell_exec`
isn't — exactly the trust upgrade dev audience needs.

## How this composes with the founder use case

Two surfaces, same backend, two audiences:

```
┌──────────────────────────────────────────────────────┐
│       Replanner + Worker + Decisions backbone        │
└──┬───────────────────────────────────┬───────────────┘
   │                                   │
   ▼                                   ▼
Founder UI (current)            Maintainer UI (added)
- new project                   - import existing repo
- Direction chat                - Issues → Requests
- Progress timeline             - PR-aware Progress
- Decisions inbox               - Decisions for repo merges
- Live preview as trust         - Test/CI/review as trust
```

Same Decisions table, same Request → Run flow, same PR generation.
The UI surface knows which mode the project is in (`projects.mode`
column?) and renders accordingly.

A single founder might use both:
- "ai-todo-app" project → founder mode (new product)
- "my-oss-lib" project → maintainer mode (their own OSS repo)

Both run on the same BSNexus tenant, same auth, same billing.

## Market mapping

The space is contested but unowned:

- **Sweep AI**: pioneer, mostly inactive now. Showed the demand exists.
- **Codegen**: well-funded, B2B, focused on enterprise repo work.
- **Cursor background agents**: power-tool for Cursor users, not
  standalone product.
- **GitHub Spark / Copilot Workspace**: GitHub's own attempt; deeply
  integrated but framed as "human + AI", not "autonomous AI".
- **Devin**: agent maintainer pitch, $500/mo, narrow audience.
- **Claude Code GitHub Action**: Anthropic's official offering;
  reactive (responds to issue/PR mentions), not proactive maintainer.

BSNexus's potential edge:
- Replanner gives multi-step autonomous loops most competitors don't have.
- Decisions inbox handles the strategic-call problem better than
  pure-automation tools (which just give up).
- Founder + maintainer modes share infrastructure, so we develop
  both for less than building two products.

The risk: dev audience is critical of AI-generated PRs. Several
high-profile OSS projects (curl, ffmpeg) have publicly rejected AI
contributions. Need to position as "AI assistant for the maintainer
of *this* repo, with maintainer's blessing", not "AI drive-by
contributor".

## When to invest

This is **not v1**. The current ship-blockers are trust uplift in
founder mode. Post-merge sprint priorities should be:

1. (Sprint 1) Trust: Q2 strengthening, ETA, founder summary.
2. (Sprint 2) Internal git: phase commits, Inside diff view.
3. (Sprint 3-4) BYO remote: GitHub OAuth, PR open, PR body templating.
4. (Sprint 5-6) **Existing project import + codebase comprehension**.
5. (Sprint 7+) Issue ingestion, triage, PR review pass, CI integration.

Total: ~3-4 months from current state to a credible OSS-maintainer
demo. Faster if we cut founder-mode polish.

## Sequencing decision (the real strategic question)

After current ship + trust sprint, there's a fork:

- **Path α — finish founder mode hard**: live preview, ETA, narration,
  decision provenance, reverse interview. Ship to first 100 founders.
  Existing project import becomes v2 after PMF signal.
- **Path β — broaden to maintainer mode early**: import existing
  projects starting sprint 5, dual-mode UI. Two audiences from day
  one.

Path α is more focused; path β has more TAM but risks neither audience
loving the product enough to evangelize.

Default: **path α**. Reasons: founder mode is what 4-surface UX was
designed for, expanding before that lands risks losing the
differentiator. But the option to pivot to β stays open because the
architecture supports it natively.

## Anti-goals

- **Don't ship "AI bot opens PRs" without maintainer opt-in.** OSS
  community blowback would poison the brand.
- **Don't auto-merge by default, ever.** Human approval gate is
  non-negotiable for the dev audience.
- **Don't position as "replacement maintainer".** Position as
  "delegate the boring parts of maintaining" — typos, deps, simple
  bugs. Hard architecture decisions stay human.
- **Don't ship the maintainer mode UI before founder mode reaches
  PMF.** Expanding scope before validating the core hypothesis is
  how products die.
