"""PR body composer (G8.4).

``compose_pr_body`` walks the Request's verified Deliverables,
resolved Decisions, and risk summaries to build a Markdown PR body
the founder can review without leaving GitHub.

Replaces the G8.3 placeholder. Sections, in order:

  1. **Summary** — Request intent verbatim (the founder's words).
  2. **Verified Deliverables** — title, type, latest verifier command
     + exit code, and the ``commit_sha`` BSNexus committed to the
     branch in G8.2 so reviewers can scan the PR commit graph and
     match deliverables to commits.
  3. **Decisions** — resolved Decisions on this Request, each with
     the question, the founder's resolution, and who resolved it.
  4. **Risks** — non-empty ``deliverable.risk_summary`` rows.
  5. **Footer** — small bsnexus signature so reviewers know this is
     an AI-company-produced PR.

Composer is pure: it reads from the session, returns a string,
performs no mutations. Idempotent in the same way as a SELECT.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.core.domain import ProofAspectType, ProofState
from backend.src.models import Decision, Deliverable, Request, VerificationAspect


async def compose_pr_body(*, request: Request, session: AsyncSession) -> str:
    """Build the Markdown PR body for ``request``. Always returns a
    string; sections with no rows are silently omitted so the PR
    doesn't carry empty headings.
    """
    parts: list[str] = []

    intent = (request.intent or "").strip()
    parts.append("## Summary\n")
    parts.append(intent if intent else "_(no intent recorded)_")
    parts.append("")

    deliverable_rows = (
        (
            await session.execute(
                select(Deliverable).where(Deliverable.request_id == request.id).order_by(Deliverable.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    verified = [d for d in deliverable_rows if d.proof_state == ProofState.verified]
    if verified:
        parts.append("## Verified Deliverables\n")
        for deliverable in verified:
            test_aspect = await _latest_aspect(session, deliverable.id, ProofAspectType.code_test)
            verifier_line = _verifier_line(test_aspect)
            commit_line = f"`{deliverable.commit_sha[:12]}`" if deliverable.commit_sha else "_not yet committed_"
            parts.append(f"### {deliverable.title or 'Untitled deliverable'}")
            parts.append(f"- Type: `{deliverable.type.value}`")
            parts.append(f"- Commit: {commit_line}")
            parts.append(f"- Verifier: {verifier_line}")
            if deliverable.summary:
                parts.append("")
                parts.append(deliverable.summary.strip())
            parts.append("")

    decisions = (
        (
            await session.execute(
                select(Decision)
                .where(Decision.request_id == request.id, Decision.resolved_at.is_not(None))
                .order_by(Decision.resolved_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if decisions:
        parts.append("## Decisions\n")
        for decision in decisions:
            who = decision.resolved_by or "founder"
            resolution = (decision.resolution or "").strip() or "_(no resolution text recorded)_"
            parts.append(f"- **{decision.question.strip()}** → {resolution} _(resolved by {who})_")
        parts.append("")

    risk_summaries = [
        deliverable.risk_summary.strip()
        for deliverable in deliverable_rows
        if deliverable.risk_summary and deliverable.risk_summary.strip()
    ]
    if risk_summaries:
        parts.append("## Risks\n")
        for risk in risk_summaries:
            parts.append(f"- {risk}")
        parts.append("")

    parts.append("---")
    parts.append(f"_Opened by BSNexus for request `{request.id}`._")

    return "\n".join(parts).rstrip() + "\n"


def _verifier_line(aspect: VerificationAspect | None) -> str:
    if aspect is None:
        return "_no verification aspect recorded_"
    command = ""
    inputs = aspect.inputs or {}
    if isinstance(inputs, dict):
        commands = inputs.get("commands")
        if isinstance(commands, list) and commands:
            first = commands[0]
            if isinstance(first, list) and first:
                command = " ".join(str(part) for part in first)
            elif isinstance(first, str):
                command = first
    exit_code = aspect.exit_code
    if command and exit_code is not None:
        return f"`{command}` (exit {exit_code})"
    if command:
        return f"`{command}`"
    if exit_code is not None:
        return f"exit {exit_code}"
    return aspect.aspect_type.value


async def _latest_aspect(
    session: AsyncSession, deliverable_id: object, aspect_type: ProofAspectType
) -> VerificationAspect | None:
    stmt = (
        select(VerificationAspect)
        .where(
            VerificationAspect.deliverable_id == deliverable_id,
            VerificationAspect.aspect_type == aspect_type,
        )
        .order_by(VerificationAspect.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()
