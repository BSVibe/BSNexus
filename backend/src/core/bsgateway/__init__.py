"""BSGateway HTTP client — single LLM/CLI entry point for BSNexus.

After the Direction reset 2026-05-03, BSNexus delegates **all** LLM and
CLI execution to BSGateway via ``POST /api/v1/chat/completions``. This
module is the only place in BSNexus that issues outbound LLM HTTP
requests — ``litellm`` is intentionally *not* a dependency.
"""

from backend.src.core.bsgateway.adapter import BSGatewayAdapter
from backend.src.core.bsgateway.client import BSGatewayClient, BSGatewayError

__all__ = ["BSGatewayAdapter", "BSGatewayClient", "BSGatewayError"]
