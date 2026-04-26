"""S4 — EncryptionManager round-trip + tamper-detection coverage.

Audit §6 cited credential-store + tamper scenarios as a test gap. The
production deployment encrypts every ``TenantIntegrationConfig.api_key``
through this manager; if the HMAC tag check ever silently regressed, an
attacker who pinned ciphertext could swap bytes undetected.

These tests cover:

  * Round-trip preservation (Unicode, large blobs, empty ascii).
  * HMAC tampering of the ciphertext or the IV.
  * Decryption with a different key (key rotation safety).
  * Mask/hash helpers used by the integrations API redaction layer.
  * ``generate_key`` randomness sanity.
"""

from __future__ import annotations

import base64

import pytest

from backend.src.core.encryption import EncryptionManager


# ── Round-trip ──────────────────────────────────────────────────────


def test_round_trip_ascii_short() -> None:
    mgr = EncryptionManager("rotation-key-1")
    cipher = mgr.encrypt_value("supersecret")
    assert mgr.decrypt_value(cipher) == "supersecret"


def test_round_trip_unicode_payload() -> None:
    mgr = EncryptionManager("rotation-key-2")
    plaintext = "비밀-한글-😀-emoji"
    cipher = mgr.encrypt_value(plaintext)
    assert mgr.decrypt_value(cipher) == plaintext


def test_round_trip_long_payload_crosses_block_boundary() -> None:
    """The XOR keystream is generated 32B at a time; ensure round-trip
    works when the payload spans many blocks (BSGateway routing context
    can be ~200B JSON snippets)."""
    mgr = EncryptionManager("rotation-key-3")
    plaintext = "x" * 500
    cipher = mgr.encrypt_value(plaintext)
    assert mgr.decrypt_value(cipher) == plaintext


def test_round_trip_empty_string() -> None:
    mgr = EncryptionManager("rotation-key-4")
    cipher = mgr.encrypt_value("")
    assert mgr.decrypt_value(cipher) == ""


# ── Tamper detection ───────────────────────────────────────────────


def test_decrypt_rejects_tampered_ciphertext() -> None:
    """Flipping a single bit in the ciphertext middle MUST fail HMAC."""
    mgr = EncryptionManager("k")
    cipher = mgr.encrypt_value("payload-A")

    raw = bytearray(base64.urlsafe_b64decode(cipher.encode()))
    # Flip a byte squarely in the ciphertext span (after IV, before tag).
    raw[20] ^= 0x01
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode()

    with pytest.raises(ValueError, match="integrity"):
        mgr.decrypt_value(tampered)


def test_decrypt_rejects_tampered_iv() -> None:
    """Flipping a bit in the IV section MUST fail HMAC."""
    mgr = EncryptionManager("k")
    cipher = mgr.encrypt_value("payload-B")

    raw = bytearray(base64.urlsafe_b64decode(cipher.encode()))
    raw[0] ^= 0x80  # IV byte 0
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode()

    with pytest.raises(ValueError):
        mgr.decrypt_value(tampered)


def test_decrypt_rejects_tampered_tag() -> None:
    """Flipping the trailing HMAC tag bytes MUST fail check."""
    mgr = EncryptionManager("k")
    cipher = mgr.encrypt_value("payload-C")

    raw = bytearray(base64.urlsafe_b64decode(cipher.encode()))
    raw[-1] ^= 0xFF
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode()

    with pytest.raises(ValueError):
        mgr.decrypt_value(tampered)


def test_decrypt_rejects_too_short_blob() -> None:
    too_short = base64.urlsafe_b64encode(b"abc").decode()
    with pytest.raises(ValueError, match="too short"):
        EncryptionManager("k").decrypt_value(too_short)


def test_decrypt_rejects_invalid_base64() -> None:
    """Strictly-bad base64 (non-ASCII bytes) must raise ValueError —
    the manager surfaces both ``too short`` and ``format`` errors as
    ``ValueError`` so callers (e.g. ``_decrypt`` in
    ``core/integrations/config.py``) can degrade to ``api_key=None``.
    """
    # Non-ASCII byte → encode("ascii") raises UnicodeEncodeError which
    # the manager wraps as ``ValueError("Invalid encrypted data format")``.
    with pytest.raises(ValueError):
        EncryptionManager("k").decrypt_value("not-base64-数据")


# ── Key rotation safety ────────────────────────────────────────────


def test_decrypt_with_different_key_fails_authentication() -> None:
    """Key rotation: a value encrypted under key A MUST NOT decrypt
    under key B. Audit §6 calls this out explicitly because integration
    api_keys are encrypted at rest and a silent mismatch would leak
    every tenant's credentials silently."""
    src = EncryptionManager("primary-key")
    dst = EncryptionManager("different-key")

    cipher = src.encrypt_value("rotation-marker")
    with pytest.raises(ValueError):
        dst.decrypt_value(cipher)


def test_round_trip_key_warning_for_default_key(caplog: pytest.LogCaptureFixture) -> None:
    """The dev default key ``dev-encryption-key-change-in-production``
    must trigger the SECURITY warning. This guard prevents quietly
    shipping prod with the default."""
    import logging

    with caplog.at_level(logging.WARNING):
        EncryptionManager("dev-encryption-key-change-in-production")
    assert any("SECURITY" in record.message for record in caplog.records)


# ── Helpers used by API redaction layer ────────────────────────────


def test_hash_value_is_deterministic_and_keyed() -> None:
    a = EncryptionManager("k1")
    b = EncryptionManager("k2")
    assert a.hash_value("X") == a.hash_value("X")
    # Different keys give different hashes (HMAC keying).
    assert a.hash_value("X") != b.hash_value("X")


def test_mask_sensitive_redacts_middle() -> None:
    masked = EncryptionManager("k").mask_sensitive("abcdef0123456789", visible_prefix=3, visible_suffix=4)
    assert masked.startswith("abc")
    assert masked.endswith("6789")
    assert "*" in masked


def test_mask_sensitive_short_string_fully_masked() -> None:
    masked = EncryptionManager("k").mask_sensitive("abc")
    assert masked == "***"


# ── generate_key ───────────────────────────────────────────────────


def test_generate_key_is_64_hex_chars() -> None:
    """generate_key returns 32 random bytes hex-encoded — 64 chars."""
    k = EncryptionManager.generate_key()
    assert len(k) == 64
    assert all(c in "0123456789abcdef" for c in k)


def test_generate_key_uniqueness() -> None:
    keys = {EncryptionManager.generate_key() for _ in range(50)}
    assert len(keys) == 50, "generate_key must be cryptographically random"
