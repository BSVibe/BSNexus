"""Tests for the .bsd canonical schema validator + normaliser."""
from __future__ import annotations

from backend.src.core.bsd_schema import (
    ALLOWED_TYPES,
    normalise_spec,
    validate_spec,
)


# ── Validation ──────────────────────────────────────────────────────


def test_accepts_canonical_screen():
    spec = {
        "type": "Screen",
        "props": {"style": {"backgroundColor": "#fff"}},
        "children": [
            {"type": "Appbar", "props": {"title": "Home"}},
            {
                "type": "ScrollView",
                "props": {"style": {"padding": 16, "gap": 12}},
                "children": [
                    {"type": "Text", "props": {"text": "Hello"}},
                    {"type": "Button", "props": {"text": "Go", "variant": "primary"}},
                ],
            },
        ],
    }
    assert validate_spec(spec) == []


def test_rejects_unknown_type():
    spec = {"type": "TodoList", "children": []}  # TodoList is not canonical
    errors = validate_spec(spec)
    assert len(errors) >= 1
    assert "TodoList" in errors[0].message
    assert "List" in errors[0].message  # suggestion


def test_rejects_bad_root():
    spec = {"type": "Text", "props": {"text": "x"}}  # root must be layout
    errors = validate_spec(spec)
    assert any("root type" in e.message for e in errors)


def test_rejects_unknown_prop():
    spec = {
        "type": "Screen",
        "children": [
            {"type": "Button", "props": {"text": "Go", "onPressColor": "red"}},
        ],
    }
    errors = validate_spec(spec)
    assert any("onPressColor" in e.message for e in errors)


def test_rejects_unknown_style_key():
    spec = {
        "type": "Screen",
        "props": {"style": {"background": "#fff"}},  # should be backgroundColor
    }
    errors = validate_spec(spec)
    assert any("background" in e.message for e in errors)


def test_accepts_string_text_child():
    spec = {
        "type": "Screen",
        "children": [{"type": "Text", "children": "Hi"}],
    }
    assert validate_spec(spec) == []


def test_unwraps_root_key():
    spec = {"root": {"type": "Screen", "children": []}}
    assert validate_spec(spec) == []


# ── Normalisation ───────────────────────────────────────────────────


def test_normalise_aliases_container_to_view():
    raw = {"type": "Container", "children": [{"type": "Text", "text": "Hi"}]}
    out = normalise_spec(raw)
    assert out["type"] == "View"


def test_normalise_folds_flat_props():
    raw = {"type": "Button", "text": "Go", "variant": "primary"}
    out = normalise_spec(raw)
    assert out["type"] == "Button"
    assert out["props"] == {"text": "Go", "variant": "primary"}


def test_normalise_folds_top_level_style():
    raw = {
        "type": "View",
        "style": {"padding": 16},
        "children": [],
    }
    out = normalise_spec(raw)
    assert out["props"]["style"] == {"padding": 16}


def test_normalise_recursive():
    raw = {
        "type": "Container",
        "children": [
            {"type": "TodoList", "children": [
                {"type": "TodoItem", "text": "Buy milk", "completed": False},
            ]},
        ],
    }
    out = normalise_spec(raw)
    assert out["type"] == "View"
    assert out["children"][0]["type"] == "List"
    assert out["children"][0]["children"][0]["type"] == "ListItem"
    assert out["children"][0]["children"][0]["props"] == {"text": "Buy milk", "completed": False}


def test_normalise_then_validate_passes():
    """The Qwen3-style ad-hoc spec should become valid after normalisation."""
    raw = {
        "type": "Screen",
        "children": [
            {"type": "Appbar", "title": "My Todos"},
            {"type": "TodoList", "children": [
                {"type": "TodoItem", "text": "Ship v1", "completed": False},
            ]},
            {"type": "Fab", "icon": "add"},
        ],
    }
    normalised = normalise_spec(raw)
    errors = validate_spec(normalised)
    assert errors == []


def test_full_canonical_vocabulary_reachable():
    """Smoke: every allowed type shows up in the canonical schema markdown
    so the Designer agent sees the full vocabulary in its prompt."""
    from backend.src.core.bsd_schema import CANONICAL_VOCAB_MARKDOWN
    for t in ALLOWED_TYPES:
        assert f"`{t}`" in CANONICAL_VOCAB_MARKDOWN, f"{t} missing from prompt"
