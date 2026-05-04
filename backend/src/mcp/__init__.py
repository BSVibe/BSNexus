"""BSNexus MCP server — runs alongside the main FastAPI app and lets
the BSGateway worker's claude CLI call back into BSNexus over MCP.

Mount point: ``/mcp/sse`` (token in query param). The dispatcher mints
a run-scoped HMAC token before each BSGateway chat completion and
embeds it in ``metadata.mcp_servers["bsnexus"].url``.

Public surface:

- :func:`issue_run_scoped_token` / :func:`verify_run_scoped_token` —
  HMAC token mint + verify (see :mod:`backend.src.mcp.auth`). Dispatcher
  calls ``issue`` to embed the token in ``metadata.mcp_servers[...]``;
  the SSE handler calls ``verify`` on every connect.
- :func:`get_decision_queue` — process-wide queue. The decisions API's
  resolve handler calls ``queue.notify`` on it.
- Tool implementations (``create_decision`` / ``wait_for_decision`` /
  ``list_run_artifacts`` / ``read_artifact`` / ``report_deliverable`` /
  ``search_knowledge``) live in :mod:`backend.src.mcp.tools` and are
  re-exported here for easier imports.

The router itself is mounted by :func:`backend.src.mcp.server.attach_to_app`,
called from ``main.create_app``.
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
    read_artifact,
    report_deliverable,
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
    "read_artifact",
    "report_deliverable",
    "search_knowledge",
    "verify_run_scoped_token",
    "wait_for_decision",
]
