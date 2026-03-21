"""Encryption manager for data protection at rest and in transit."""

import base64
import hashlib
import hmac
import os
import secrets


class EncryptionManager:
    """Manages encryption for sensitive data at rest.

    Uses HMAC-SHA256 in counter mode as a stream cipher with HMAC authentication
    for field-level encryption of sensitive data (API keys, tokens, etc.).

    WARNING: This is a stdlib-only implementation suitable for low-sensitivity data.
    For production with high-sensitivity data, replace with `cryptography.fernet.Fernet`
    or AES-256-GCM via the `cryptography` package.

    Ensure ENCRYPTION_KEY is set via environment variable and rotated periodically.
    """

    def __init__(self, key: str) -> None:
        if not key or key == "dev-encryption-key-change-in-production":
            import logging

            logging.getLogger(__name__).warning(
                "SECURITY: Using default encryption key. Set ENCRYPTION_KEY environment variable for production."
            )
        # Derive a 32-byte key from the provided key string
        self._key = hashlib.sha256(key.encode()).digest()

    def encrypt_value(self, plaintext: str) -> str:
        """Encrypt a string value for storage.

        Uses XOR stream cipher with HMAC authentication.
        Returns base64-encoded ciphertext with embedded IV and HMAC tag.
        """
        iv = os.urandom(16)
        plaintext_bytes = plaintext.encode("utf-8")

        # Generate keystream using HMAC-SHA256 in counter mode
        ciphertext = self._xor_encrypt(plaintext_bytes, iv)

        # Compute HMAC over IV + ciphertext for authentication
        tag = hmac.new(self._key, iv + ciphertext, hashlib.sha256).digest()

        # Format: base64(iv + ciphertext + tag)
        combined = iv + ciphertext + tag
        return base64.urlsafe_b64encode(combined).decode("ascii")

    def decrypt_value(self, encrypted: str) -> str:
        """Decrypt a previously encrypted value.

        Raises ValueError if the data is tampered with or the key is wrong.
        """
        try:
            combined = base64.urlsafe_b64decode(encrypted.encode("ascii"))
        except Exception as e:
            raise ValueError("Invalid encrypted data format") from e

        if len(combined) < 48:  # 16 (IV) + 0 (min data) + 32 (HMAC)
            raise ValueError("Invalid encrypted data: too short")

        iv = combined[:16]
        tag = combined[-32:]
        ciphertext = combined[16:-32]

        # Verify HMAC before decrypting
        expected_tag = hmac.new(self._key, iv + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected_tag):
            raise ValueError("Decryption failed: data integrity check failed")

        plaintext_bytes = self._xor_encrypt(ciphertext, iv)
        return plaintext_bytes.decode("utf-8")

    def _xor_encrypt(self, data: bytes, iv: bytes) -> bytes:
        """XOR encrypt/decrypt data using HMAC-SHA256 keystream."""
        result = bytearray()
        block_count = (len(data) + 31) // 32

        for i in range(block_count):
            counter = i.to_bytes(4, "big")
            keystream_block = hmac.new(self._key, iv + counter, hashlib.sha256).digest()
            start = i * 32
            end = min(start + 32, len(data))
            for j in range(end - start):
                result.append(data[start + j] ^ keystream_block[j])

        return bytes(result)

    def hash_value(self, value: str) -> str:
        """Create a one-way hash of a value (for indexing encrypted fields)."""
        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def generate_key() -> str:
        """Generate a secure random encryption key."""
        return secrets.token_hex(32)

    def mask_sensitive(self, value: str, *, visible_prefix: int = 3, visible_suffix: int = 4) -> str:
        """Mask a sensitive value, showing only prefix and suffix."""
        if len(value) <= visible_prefix + visible_suffix:
            return "*" * len(value)
        masked_len = len(value) - visible_prefix - visible_suffix
        return value[:visible_prefix] + "*" * masked_len + value[-visible_suffix:]
