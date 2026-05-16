"""Verification Contract schema — parsing / normalization tests.

Pins the contract from
``~/Docs/BSNexus_Verification_Contract_Design_2026-05-17.md``: the work
LLM declares a contract (a list of ``command`` / ``judge`` checks); the
parser is tolerant of imperfect LLM JSON — it drops invalid checks and
returns ``None`` only when nothing usable remains.
"""

from __future__ import annotations

from backend.src.core.verification_contract import (
    VerificationCheck,
    VerificationContract,
    parse_verification_contract,
)


def test_parse_command_check():
    contract = parse_verification_contract(
        {"checks": [{"kind": "command", "command": "uv run pytest", "rationale": "unit tests"}]}
    )
    assert contract is not None
    assert len(contract.checks) == 1
    check = contract.checks[0]
    assert check.kind == "command"
    assert check.command == "uv run pytest"
    assert check.criteria == ()
    assert check.rationale == "unit tests"


def test_parse_judge_check():
    contract = parse_verification_contract(
        {
            "checks": [
                {
                    "kind": "judge",
                    "criteria": ["README documents every flag", "quickstart is runnable"],
                    "rationale": "doc quality",
                }
            ]
        }
    )
    assert contract is not None
    check = contract.checks[0]
    assert check.kind == "judge"
    assert check.criteria == ("README documents every flag", "quickstart is runnable")
    assert check.command is None


def test_parse_mixed_checks():
    contract = parse_verification_contract(
        {
            "checks": [
                {"kind": "command", "command": "go test ./...", "rationale": ""},
                {"kind": "judge", "criteria": ["covers the spec"], "rationale": ""},
            ]
        }
    )
    assert contract is not None
    assert [c.kind for c in contract.checks] == ["command", "judge"]


def test_parse_drops_command_check_without_command():
    contract = parse_verification_contract(
        {
            "checks": [
                {"kind": "command", "command": "", "rationale": "blank"},
                {"kind": "command", "command": "pytest", "rationale": "good"},
            ]
        }
    )
    assert contract is not None
    assert len(contract.checks) == 1
    assert contract.checks[0].command == "pytest"


def test_parse_drops_judge_check_without_criteria():
    contract = parse_verification_contract(
        {
            "checks": [
                {"kind": "judge", "criteria": [], "rationale": "empty"},
                {"kind": "judge", "criteria": ["  ", ""], "rationale": "blank-only"},
            ]
        }
    )
    assert contract is None  # no usable check remains


def test_parse_drops_unknown_kind():
    contract = parse_verification_contract(
        {"checks": [{"kind": "telepathy", "rationale": "nope"}, {"kind": "command", "command": "pytest"}]}
    )
    assert contract is not None
    assert len(contract.checks) == 1


def test_parse_returns_none_for_garbage():
    assert parse_verification_contract(None) is None
    assert parse_verification_contract("not a dict") is None
    assert parse_verification_contract({}) is None
    assert parse_verification_contract({"checks": "not a list"}) is None
    assert parse_verification_contract({"checks": []}) is None


def test_parse_strips_and_dedups_criteria():
    contract = parse_verification_contract(
        {"checks": [{"kind": "judge", "criteria": ["  a  ", "a", "b", ""], "rationale": ""}]}
    )
    assert contract is not None
    assert contract.checks[0].criteria == ("a", "b")


def test_rationale_defaults_to_empty():
    contract = parse_verification_contract({"checks": [{"kind": "command", "command": "pytest"}]})
    assert contract is not None
    assert contract.checks[0].rationale == ""


def test_roundtrip_to_dict_and_back():
    original = parse_verification_contract(
        {
            "checks": [
                {"kind": "command", "command": "pytest", "rationale": "tests"},
                {"kind": "judge", "criteria": ["x", "y"], "rationale": "judge"},
            ]
        }
    )
    assert original is not None
    restored = parse_verification_contract(original.to_dict())
    assert restored == original


def test_has_command_and_judge_checks():
    contract = parse_verification_contract(
        {
            "checks": [
                {"kind": "command", "command": "pytest"},
                {"kind": "judge", "criteria": ["x"]},
            ]
        }
    )
    assert contract is not None
    assert [c for c in contract.checks if c.kind == "command"]
    assert [c for c in contract.checks if c.kind == "judge"]


def test_check_is_frozen():
    check = VerificationCheck(kind="command", command="pytest", criteria=(), rationale="")
    contract = VerificationContract(checks=(check,))
    assert contract.checks[0] is check
