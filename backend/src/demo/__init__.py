"""BSNexus demo backend module — public interactive demo support.

Most logic comes from the shared ``bsvibe-demo`` package:
- ``mint_demo_jwt`` / ``decode_demo_jwt`` (JWT issuer)
- ``is_demo_mode`` / ``enforce_demo_llm_mock`` (LLM block)
- ``DemoSessionServiceSqlAlchemy`` (session creation, BSNexus uses SQLA)
- ``demo_gc_sqlalchemy`` (hourly cron)

Per-product pieces stay here:
- :mod:`backend.src.demo.seed` — BSNexus-specific demo data
  (project, agents, plan tree, decisions)
- :mod:`backend.src.demo.auth` — wires demo JWT to BSNexus's existing
  ``request.state.tenant_id`` pattern
- :mod:`backend.src.demo.router` — POST /api/v1/demo/session endpoint
"""
