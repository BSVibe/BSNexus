"""Tests for EncryptionManager."""

import pytest

from backend.src.core.encryption import EncryptionManager


class TestEncryptDecrypt:
    def test_round_trip(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        plaintext = "Hello, World!"
        encrypted = enc.encrypt_value(plaintext)
        decrypted = enc.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_different_ciphertexts_each_time(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        plaintext = "same-input"
        encrypted1 = enc.encrypt_value(plaintext)
        encrypted2 = enc.encrypt_value(plaintext)
        assert encrypted1 != encrypted2  # Random IV means different ciphertext

    def test_empty_string(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        encrypted = enc.encrypt_value("")
        decrypted = enc.decrypt_value(encrypted)
        assert decrypted == ""

    def test_long_string(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        plaintext = "A" * 10000
        encrypted = enc.encrypt_value(plaintext)
        decrypted = enc.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_unicode_content(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        plaintext = "Hello 🌍 World 你好"
        encrypted = enc.encrypt_value(plaintext)
        decrypted = enc.decrypt_value(encrypted)
        assert decrypted == plaintext

    def test_wrong_key_fails(self):
        enc1 = EncryptionManager("key-one-for-encryption-test-here")
        enc2 = EncryptionManager("key-two-for-encryption-test-here")
        encrypted = enc1.encrypt_value("secret")
        with pytest.raises(ValueError, match="integrity check failed"):
            enc2.decrypt_value(encrypted)

    def test_tampered_data_fails(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        encrypted = enc.encrypt_value("secret")
        # Tamper with the ciphertext
        import base64
        raw = bytearray(base64.urlsafe_b64decode(encrypted))
        raw[20] ^= 0xFF  # Flip a byte
        tampered = base64.urlsafe_b64encode(bytes(raw)).decode()
        with pytest.raises(ValueError, match="integrity check failed"):
            enc.decrypt_value(tampered)

    def test_invalid_base64_fails(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        with pytest.raises(ValueError, match="Invalid encrypted data"):
            enc.decrypt_value("not-valid-base64!!!")

    def test_too_short_data_fails(self):
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        import base64
        short = base64.urlsafe_b64encode(b"short").decode()
        with pytest.raises(ValueError, match="too short"):
            enc.decrypt_value(short)


class TestHashValue:
    def test_consistent_hash(self):
        enc = EncryptionManager("test-key")
        hash1 = enc.hash_value("test-value")
        hash2 = enc.hash_value("test-value")
        assert hash1 == hash2

    def test_different_values_different_hashes(self):
        enc = EncryptionManager("test-key")
        hash1 = enc.hash_value("value-1")
        hash2 = enc.hash_value("value-2")
        assert hash1 != hash2

    def test_different_keys_different_hashes(self):
        enc1 = EncryptionManager("key-one")
        enc2 = EncryptionManager("key-two")
        hash1 = enc1.hash_value("same-value")
        hash2 = enc2.hash_value("same-value")
        assert hash1 != hash2


class TestMaskSensitive:
    def test_mask_api_key(self):
        enc = EncryptionManager("test-key")
        masked = enc.mask_sensitive("sk-1234567890abcdef")
        assert masked.startswith("sk-")
        assert masked.endswith("cdef")
        assert "1234567890" not in masked

    def test_mask_short_string(self):
        enc = EncryptionManager("test-key")
        masked = enc.mask_sensitive("short")
        assert masked == "*****"

    def test_mask_custom_visible(self):
        enc = EncryptionManager("test-key")
        masked = enc.mask_sensitive("1234567890", visible_prefix=2, visible_suffix=2)
        assert masked == "12******90"


class TestGenerateKey:
    def test_generates_unique_keys(self):
        key1 = EncryptionManager.generate_key()
        key2 = EncryptionManager.generate_key()
        assert key1 != key2

    def test_key_length(self):
        key = EncryptionManager.generate_key()
        assert len(key) == 64  # 32 bytes = 64 hex chars


class TestDefaultKeyWarning:
    def test_warns_on_default_key(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            EncryptionManager("dev-encryption-key-change-in-production")
        assert any("default encryption key" in r.message for r in caplog.records)


class TestDecryptErrors:
    def test_decrypt_invalid_base64_raises(self):
        """decrypt_value with invalid base64 raises ValueError."""
        enc = EncryptionManager("test-key-for-encryption-testing-32chars")
        with pytest.raises(ValueError, match="Invalid encrypted data format"):
            enc.decrypt_value("not!!valid!!base64!!!")
