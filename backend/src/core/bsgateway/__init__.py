"""BSGateway client substrate.

The greenfield reset deletes the legacy ExecutionRun adapter. The raw
client remains REVIEW_LATER substrate for a future execution boundary.
"""

from backend.src.core.bsgateway.client import BSGatewayClient, BSGatewayError

__all__ = ["BSGatewayClient", "BSGatewayError"]
