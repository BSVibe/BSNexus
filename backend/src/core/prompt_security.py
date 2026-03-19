import hashlib
import hmac
import json
import time
import uuid


class PromptSigner:
    """HMAC-SHA256 based prompt signing and verification."""

    def __init__(self, secret_key: str) -> None:
        self.secret_key = secret_key.encode()

    def sign(self, prompt: str) -> dict:
        """Sign a prompt with HMAC-SHA256."""
        nonce = str(uuid.uuid4())
        timestamp = int(time.time())

        payload = json.dumps(
            {
                "prompt": prompt,
                "nonce": nonce,
                "timestamp": timestamp,
            },
            sort_keys=True,
        )

        signature = hmac.new(
            self.secret_key,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return {
            "prompt": prompt,
            "signature": signature,
            "nonce": nonce,
            "timestamp": timestamp,
        }

    def verify(self, signed_prompt: dict, max_age: int = 3600) -> bool:
        """Verify a signed prompt."""
        # 1. Check required fields
        required = {"prompt", "signature", "nonce", "timestamp"}
        if not required.issubset(signed_prompt.keys()):
            return False

        # 2. Check timestamp validity (within max_age seconds)
        age = int(time.time()) - signed_prompt["timestamp"]
        if age > max_age or age < 0:
            return False

        # 3. Recompute HMAC and compare
        payload = json.dumps(
            {
                "prompt": signed_prompt["prompt"],
                "nonce": signed_prompt["nonce"],
                "timestamp": signed_prompt["timestamp"],
            },
            sort_keys=True,
        )

        expected = hmac.new(
            self.secret_key,
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signed_prompt["signature"])

    def extract_prompt(self, signed_prompt: dict) -> str | None:
        """Verify and extract the prompt. Returns None if verification fails."""
        if self.verify(signed_prompt):
            return signed_prompt["prompt"]
        return None
