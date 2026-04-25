"""Tests for the YAML-backed prompt registry.

The registry externalizes hardcoded LLM prompts into versioned YAML
files so they can be iterated on, A/B tested via environment-variable
variant selection, and eventually moved to a DB without changing
callsite code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.src.core.prompts.registry import (
    PromptNotFound,
    PromptRegistry,
    load_prompt,
    load_persona_list,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_global_cache():
    reset_cache()
    yield
    reset_cache()


def _write_prompt(tmp_path: Path, name: str, body: dict) -> Path:
    import yaml

    f = tmp_path / f"{name}.yaml"
    f.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return f


def test_loads_default_variant_body(tmp_path):
    _write_prompt(
        tmp_path,
        "replanner",
        {
            "name": "replanner",
            "version": 1,
            "variants": {
                "default": {"body": "default body"},
            },
        },
    )

    reg = PromptRegistry(tmp_path)
    assert reg.load("replanner") == "default body"


def test_variant_override_via_env_var(tmp_path, monkeypatch):
    _write_prompt(
        tmp_path,
        "replanner",
        {
            "name": "replanner",
            "variants": {
                "default": {"body": "DEFAULT"},
                "experiment_a": {"body": "VARIANT_A"},
            },
        },
    )

    monkeypatch.setenv("PROMPT_VARIANT_REPLANNER", "experiment_a")

    reg = PromptRegistry(tmp_path)
    assert reg.load("replanner") == "VARIANT_A"


def test_unknown_variant_falls_back_to_default(tmp_path, monkeypatch):
    _write_prompt(
        tmp_path,
        "replanner",
        {
            "name": "replanner",
            "variants": {"default": {"body": "DEFAULT"}},
        },
    )

    monkeypatch.setenv("PROMPT_VARIANT_REPLANNER", "nope_does_not_exist")

    reg = PromptRegistry(tmp_path)
    assert reg.load("replanner") == "DEFAULT"


def test_missing_prompt_raises(tmp_path):
    reg = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFound):
        reg.load("nonexistent")


def test_missing_default_variant_raises(tmp_path):
    _write_prompt(
        tmp_path,
        "broken",
        {"name": "broken", "variants": {"only_a": {"body": "x"}}},
    )

    reg = PromptRegistry(tmp_path)
    with pytest.raises(PromptNotFound):
        reg.load("broken")


def test_caches_subsequent_loads(tmp_path):
    p = _write_prompt(
        tmp_path,
        "replanner",
        {"name": "replanner", "variants": {"default": {"body": "A"}}},
    )

    reg = PromptRegistry(tmp_path)
    assert reg.load("replanner") == "A"

    p.write_text(
        "name: replanner\nvariants:\n  default:\n    body: B\n",
        encoding="utf-8",
    )

    assert reg.load("replanner") == "A"

    reg.reset()
    assert reg.load("replanner") == "B"


def test_variant_name_normalization_uppercases_in_env(tmp_path, monkeypatch):
    _write_prompt(
        tmp_path,
        "request-classifier",
        {
            "name": "request-classifier",
            "variants": {
                "default": {"body": "DEFAULT"},
                "v2": {"body": "V2"},
            },
        },
    )

    monkeypatch.setenv("PROMPT_VARIANT_REQUEST_CLASSIFIER", "v2")

    reg = PromptRegistry(tmp_path)
    assert reg.load("request-classifier") == "V2"


def test_load_persona_list(tmp_path):
    _write_prompt(
        tmp_path,
        "worker-personas",
        {
            "name": "worker-personas",
            "variants": {
                "default": {
                    "personas": [
                        {
                            "name": "builder",
                            "system_prompt_template": "BUILD {context}",
                            "tools": ["file_read"],
                            "keywords": ["build"],
                            "default_fit": 0.6,
                        },
                        {
                            "name": "analyst",
                            "system_prompt_template": "ANALYZE {context}",
                            "tools": ["file_read"],
                            "keywords": ["research"],
                            "default_fit": 0.5,
                        },
                    ]
                }
            },
        },
    )

    reg = PromptRegistry(tmp_path)
    personas = reg.load_personas("worker-personas")

    assert len(personas) == 2
    assert personas[0]["name"] == "builder"
    assert "BUILD" in personas[0]["system_prompt_template"]
    assert personas[1]["keywords"] == ["research"]


def test_module_level_helpers_use_default_dir():
    """The module-level ``load_prompt`` reads from the bundled
    ``backend/src/core/prompts/templates`` directory. The shipped
    files must contain at minimum the prompts the codebase needs."""
    body = load_prompt("replanner")
    assert "chief-of-staff" in body.lower()
    assert len(body) > 500

    classifier_body = load_prompt("request-classifier")
    assert "chit_chat" in classifier_body
    assert "modification" in classifier_body

    shared_policy = load_prompt("worker-shared-policy")
    assert "founder" in shared_policy.lower()

    personas = load_persona_list("worker-personas")
    assert any(p["name"] == "builder" for p in personas)
    assert any(p["name"] == "generalist" for p in personas)


def test_replanner_prompt_includes_intent_decomposition_rule():
    """Rule 0 (INTENT DECOMPOSITION) must be present so the replanner
    doesn't declare ``done`` after one phase when the founder named
    multiple sub-deliverables."""
    body = load_prompt("replanner")
    assert "INTENT DECOMPOSITION" in body
    assert "sub-deliverable" in body.lower()
