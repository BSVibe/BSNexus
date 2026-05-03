"""BSNexus MCP server — runs alongside the main FastAPI app and lets
the BSGateway worker's claude CLI call back into BSNexus over MCP.

Mount point: ``/mcp/sse`` (token in query param). The dispatcher mints
a run-scoped HMAC token before each BSGateway chat completion and
embeds it in ``metadata.mcp_servers["bsnexus"].url``.

Public surface:

- :func:`mint_run_scoped_token_for_metadata` — call from the dispatcher
  after a Run is loaded; produces the ``mcp_servers`` dict to pass to
  :class:`backend.src.core.bsgateway.BSGatewayAdapter.set_mcp_servers`.
- :func:`build_mcp_router` — returns the FastAPI router that mounts
  the SSE endpoint and the resolve callback bridge.
- :data:`get_decision_queue` — process-wide queue. The decisions API's
  resolve handler calls ``notify`` on it.
"""

from backend.src.mcp.auth import (
    MCPAuthError,
    issue_run_scoped_token,
    verify_run_scoped_token,
)
from backend.src.mcp.decision_queue import (
    DecisionQueue,
    DecisionWaitTimeout,
    get_decision_queue,
)
from backend.src.mcp.tools import (
    MCPToolError,
    create_decision,
    list_run_artifacts,
    search_knowledge,
    wait_for_decision,
)

__all__ = [
    "DecisionQueue",
    "DecisionWaitTimeout",
    "MCPAuthError",
    "MCPToolError",
    "create_decision",
    "get_decision_queue",
    "issue_run_scoped_token",
    "list_run_artifacts",
    "search_knowledge",
    "verify_run_scoped_token",
    "wait_for_decision",
]
