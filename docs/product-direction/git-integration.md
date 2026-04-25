# Git / PR Integration — Product Direction Note

## Why this matters

Conductor's core trust mechanism is `git diff + PR review`. BSNexus's
current trust mechanism is "the Progress timeline says delivered" —
which we just learned is insufficient (broken frontend shipped as
`exit=0` this session). The gap isn't fixed by adding more chat polish.

But we can add Conductor's mechanism *on top of* BSNexus's, without
becoming Conductor:

- **Founder surface stays**: Direction / Progress / Decisions. The
  founder who can't read code never sees git.
- **Power-user surface added**: Each request becomes a PR. Each phase
  becomes a commit. Devs (or future-self / a dev friend / a
  contractor) can review at PR granularity.
- **Same backend, two surfaces.** Like GitHub's web UI vs CLI — same
  data, different audiences.

Crucially this is **not pivoting toward Conductor**. The autonomy
thesis ("founder is asleep, AI works") gets *stronger*, because:

- Founder wakes up to N PRs ready, not N "delivered" pills.
- Skim founder summary → fast path (founder mode).
- Click into diff → verify path (graduate mode).
- Forward PR URL → escape hatch path (hand to a dev).

Three paths for the same artifact. Today there's only one.

## What "git integration" should actually deliver

### 1. Per-phase commits, per-request branches, per-request PR

Mapping:

```
Request                  →  branch:  request/<short-id>-<slug>
Phase (ExecutionRun)     →  commit:  "phase: <phase_name>" + files_written
Replanner picks done     →  PR:      "Founder request: <intent_summary>"
                                     + body: chain summary + decisions resolved
```

Implementation hooks already partly exist:

- `core/storage/git_storage.py` wraps existing `core/git_ops.py` for
  code deliverables. Today it operates on the workspace dir; needs
  to also push to a remote.
- `ExecutionRun.branch_name` and `commit_hash` columns already live in
  the schema (founder-metaphor migration).
- `Deliverable` has `created_by_run_id` — provenance link to the
  commit is one column away.

What's missing:

- Bind the workspace dir as a real git repo on first request.
- Worker-driven commits per phase (vs what we have today: file writes
  pile up in a working tree with no commits).
- PR creation against a configured remote (GitHub/GitLab).

### 2. BYO remote (founder's repo) — primary mode

The repo lives in **the user's GitHub / GitLab**, not ours. This is
how we earn trust and avoid lock-in fear:

- New project setup wizard: "Where should I push your code? [GitHub OAuth] [GitLab OAuth] [Skip — keep it in BSNexus]"
- We push commits to their remote with a deploy-key-style scoped token.
- They can audit, fork, leave anytime. Their code is theirs.

This is the inverse of Lovable / Replit, where the code is held
hostage in the platform's runtime.

### 3. Hosted-fallback mode (founder has no GitHub)

For founders who don't have a git remote: BSNexus hosts an internal
git server (Gitea / SourceHut-self-hosted / just bare repos behind
the API). They can:

- Browse via the Inside panel (a basic web tree view).
- Export with one click → "send me the zip" or "push to my new GitHub repo".

Goal: never block a founder who doesn't yet have GitHub.

### 4. Decisions linked to commits

When a `Decision` row resolves with founder input, the resolution
gets recorded as a commit message annotation:

```
phase: Add error handling middleware

Resolves: Decision a4b2 — "PostgreSQL or in-memory?" → "in-memory"
Worker followed up by [...]
```

This makes Inside panel + git history a single coherent narrative:
"why did the AI choose this?" → look at the commit message and
linked decision.

## How this beats current Conductor

Conductor's PR is "here are the files this Claude Code task changed".
That's it. BSNexus PR can be richer because the orchestration layer
knows more:

- **Why each phase**: replanner's `founder_message` per phase →
  commit messages explain the *narrative*, not just the diff.
- **Decisions resolved on this branch**: linked in PR body.
- **Trust signals**: Q2 verification result (live preview URL,
  test exit code) embedded in PR body. The PR isn't just a diff,
  it's a deliverable with proof attached.
- **Cross-deliverable PRs**: A single request might span `code` +
  `doc` + `design` deliverables. Conductor's PR is code-only;
  BSNexus's can package the design doc and the implementation in
  one PR with the founder's intent on top.

## How this composes with the trust roadmap

[visibility-and-trust.md](./visibility-and-trust.md) listed six trust
uplifts. Git integration changes the cost-benefit on most:

| Item from trust doc | Without git | With git |
|---|---|---|
| Live preview URL | Spawn ephemeral runtime | Same — PR has preview link in body (Vercel/Render style) |
| Q2 self-verification | Trust the orchestrator | Q2 result is a CI check on the PR — third-party-verifiable |
| Founder-narration summary | In Direction chat | In PR body — survives outside BSNexus |
| Decision provenance | Inside panel only | Commit message + PR body — permanent record |

Net: every trust mechanism gets stronger when paired with git, because
the artifacts persist in a tool the user already knows and trusts.

## What this is NOT

- **Not a Conductor clone.** Conductor's audience is devs reviewing
  PRs as their primary workflow. BSNexus's audience is founders;
  PRs are the optional graduate / escape path. The default UX
  surface stays Progress / Decisions.
- **Not a feature to win Conductor users.** We won't. Conductor
  ships a polished desktop app + Claude Code direct integration.
  Git layer is for the founder's *own* benefit (escape hatch +
  trust + portability), not for converting power users.
- **Not a v1 ship-blocker.** Founders who don't have GitHub yet
  shouldn't be blocked from using BSNexus. Hosted fallback first,
  BYO-remote second.

## Open design questions

These need answers before serious implementation. Surfacing now so
they're not invisible later:

1. **Branch strategy for parallel requests on one project**: linear
   chain (each request bumps `main`) vs feature branches (multiple
   requests in flight at once)? Founder UX says linear; replanner
   capacity says branches. Probably branches with auto-rebase.
2. **What happens when the founder edits files manually in their
   GitHub UI?** Three options: refuse to merge (force AI to rebase),
   accept (fold their changes into worker context), or warn. Need to
   pick one default.
3. **Non-code deliverables in git**: design `.bsd`, marketing
   `.md`, data `.json` — all in the same repo, or separate? Same
   repo simpler; separate avoids dev confusion. Probably same with
   a `.bsnexus/` convention dir for non-code.
4. **Identity of commits**: worker writes `Worker <BSNexus> <noreply@bsnexus.dev>`?
   Or impersonate the founder? Probably the former, with a
   `Generated-By: BSNexus run <id>` trailer.
5. **Cost of inactive repos**: GitHub free tier has limits per user;
   GitLab is more generous. Hosting fallback has its own storage
   cost. Need a retention policy ("after 90 days idle, archive →
   download bundle").

## Order of operations (suggestion)

Pre-merge: nothing required.

Post-merge sprint 2 (after trust sprint 1):
- Internal git per workspace (no remote yet) — every phase = commit.
- Inside panel shows commit history + diffs per phase.
- This alone gives "what changed in phase X?" answer that Inside
  currently can't.

Post-merge sprint 3-4:
- BYO remote (GitHub OAuth) → push branches + open PRs.
- PR body templating (founder summary, decisions, Q2 results).

v2 / future:
- Hosted-fallback mode for founders without GitHub.
- Bidirectional sync (founder edits → fold into worker context).

## Anti-goals

- **Don't expose git as the default UX surface.** It's a graduate /
  escape path. Founder mode stays as-is.
- **Don't require git knowledge to use BSNexus.** A founder who
  doesn't know what a branch is should still be able to ship.
- **Don't replicate every Conductor feature.** Worktree-per-task,
  parallel slots, etc. — those serve devs running Claude Code. Our
  audience doesn't need them; our orchestration layer (replanner)
  fills that role differently.
