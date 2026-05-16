"""System + user-prompt templates for the CoT decomposer.

The system prompt asks for a chain-of-thought *think first* pass before
emitting JSON. We intentionally accept that the model may wrap its
output in a ```json fence or prefix it with reasoning — the parser
handles both. Asking for "pure JSON only" would be more brittle: small
local models often hedge with a sentence of prose regardless.
"""

from __future__ import annotations

from backend.src.core.planning.context import ProjectContext


SYSTEM_PROMPT = """\
You are reviewing a single Request from the founder of an AI-native
company before any code is written. Decide how to structure the work
as a list of WorkSteps.

Think step by step about:
  - what concrete deliverables this Request produces,
  - whether there are natural checkpoints where one part needs to land
    cleanly before the next can start (e.g. schema → endpoints → UI),
  - whether the whole thing fits inside a single tight commit/PR.

Then output a JSON array of steps. Rules:

1. If the work is genuinely simple — one cohesive deliverable, no
   internal dependency chain — return exactly ONE step. Do not invent
   ceremony.
2. If the work has clear sequential dependencies, return one step per
   checkpoint, in execution order.
3. Each step must be an object with these fields:
     - "name": short label, <= 80 chars
     - "objective": one-paragraph description of what this step
       achieves
     - "expected_outputs": list of files or behaviours this step must
       produce
4. Maximum {max_steps} steps. If the Request would need more, return
   {max_steps} and let the remainder be a follow-up split.
5. EVERY step must produce a concrete code or file deliverable. NEVER
   create a step whose only job is to run tests, verify, validate, or
   "check that everything works" — an automated verifier runs pytest +
   ruff after EVERY step already. A step like "Run and validate tests"
   or "Verify endpoint functionality" is invalid: it produces nothing
   and wastes a step slot. Testing is done *inside* each implementation
   step (write the test alongside the code), not as a separate step.
6. Prefer FEWER, meatier steps over many thin ones. A step should be a
   natural commit-sized unit of work. "Set up pyproject.toml" alone is
   too thin — fold project scaffolding into the first real step.

It is fine to think out loud before the JSON. Wrap the final JSON in a
```json fenced block if you want — both fenced and bare JSON are
accepted. Do not return anything other than the JSON array as the final
structured output.
"""


def render_decomposer_messages(ctx: ProjectContext, *, max_steps: int) -> list[dict[str, str]]:
    """Build the chat messages for one decomposer call."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(max_steps=max_steps)},
        {"role": "user", "content": ctx.render()},
    ]
