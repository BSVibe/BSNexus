from __future__ import annotations

import pytest

from backend.src.prompts.loader import _cache, get_prompt, load_prompts


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the prompt cache before each test."""
    _cache.clear()
    yield
    _cache.clear()


def test_load_review_prompts():
    prompts = load_prompts("review")
    assert isinstance(prompts, dict)
    assert "code_review" in prompts


def test_load_prompts_cached():
    prompts1 = load_prompts("review")
    prompts2 = load_prompts("review")
    assert prompts1 is prompts2


def test_load_nonexistent_file():
    with pytest.raises(FileNotFoundError, match="Prompt file not found"):
        load_prompts("nonexistent")


def test_get_prompt_returns_string():
    prompt = get_prompt("review", "code_review")
    assert isinstance(prompt, str)
    assert prompt  # non-empty


def test_get_prompt_strips_whitespace():
    prompt = get_prompt("review", "code_review")
    assert not prompt.startswith("\n")
    assert not prompt.endswith("\n")


def test_get_nonexistent_key():
    with pytest.raises(KeyError, match="Prompt key 'nonexistent' not found"):
        get_prompt("review", "nonexistent")


def test_get_prompt_from_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        get_prompt("nonexistent", "system")
