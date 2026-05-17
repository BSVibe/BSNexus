# E2E Checklist — Phase A: Contract adequacy via prompt engineering

Design: `~/Docs/BSNexus_P3_Sandbox_and_Contract_Adequacy_Design_2026-05-17.md`

Phase A = **A1** (strengthen `declare_verification` tool description +
work-phase system prompt so a test step yields a contract that RUNS
the tests) + **A4** (green-before-finish ties to the declared command
checks). Prompt-only change; no schema, no endpoint.

## Unit (pinned, automated)

- [x] `_build_messages` system prompt instructs that a compile/import
  check does not exercise behaviour and the contract must RUN the test
  runner (`test_build_messages_system_prompt_requires_running_tests_not_compiling`).
- [x] `_build_messages` green-before-finish points at "every command
  check you declared" (`test_build_messages_green_before_finish_ties_to_declared_checks`).
- [x] `declare_verification` tool description warns against
  compile-only checks and carries the weak `py_compile` example
  (`test_declare_verification_description_warns_against_compile_only_checks`).
- [x] Full backend suite green (588 passed, 2 skipped); ruff clean.

## Behavioral (post-merge prod dogfood — loop policy step 4–5)

Verified by restarting dogfooding from the beginning after merge +
autodeploy. The informing-dogfood baseline (2026-05-17) is the
negative case to turn around:

- [ ] A Direction that asks for a pytest test file → the work LLM's
  declared `verification_contract` contains a `command` check that
  RUNS the tests (`pytest …`), NOT a `py_compile`-only check.
- [ ] Inspect the RunAttempt `verification_contract` for several
  Directions; confirm test steps declare a test-runner command.
- [ ] If weak (`py_compile`-only) contracts still appear → Phase A′
  (A2 contract QA / A3 verify-time gate) is re-opened per the design.

> Behavioral verification needs a real qwen3 work loop, so it runs as
> the post-merge dogfood restart, not a pre-PR step — the change is
> prompt-text-only and fully unit-pinned above.
