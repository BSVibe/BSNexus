"""Stage 4-full smoke against a branch test stack on :8200.

Pre-merge gate that runs the actual prod docker image (built from the
PR branch tip) end-to-end against the local Mac Mini ollama, before
the change touches main.

How to use::

    # 1. From the worktree containing the branch under test:
    docker build -f deploy/Dockerfile -t bsnexus-app:branch-test .

    # 2. Provision a fresh test database in the running prod postgres
    #    (isolated from prod data):
    docker exec bsnexus-postgres psql -U bsnexus -d bsnexus -c \\
      "CREATE DATABASE bsnexus_branch_test;"

    # 3. Run the branch image attached to bsnexus-network with prod
    #    secrets but the test DB:
    source ~/Works/BSNexus/main/deploy/.env
    docker run -d --name bsnexus-app-branch-test \\
      --network bsnexus-network -p 8200:8000 \\
      --env-file ~/Works/BSNexus/main/deploy/.env \\
      -e DATABASE_URL="postgresql+asyncpg://bsnexus:$POSTGRES_PASSWORD@bsnexus-postgres:5432/bsnexus_branch_test" \\
      -e REDIS_URL="redis://bsnexus-redis:6379" \\
      bsnexus-app:branch-test

    # 4. Refresh storageState (1h JWT TTL) — see frontend/e2e/specs/_populate-prod-storage.spec.ts
    cd frontend && BSVIBE_TEST_EMAIL=user@bsvibe.dev BSVIBE_TEST_PASSWORD=... \\
      pnpm exec playwright test e2e/specs/_populate-prod-storage.spec.ts

    # 5. Drive the full Stage 4 chain against :8200:
    python3 scripts/branch-stack-stage4-smoke.py

    # 6. Tear down:
    docker rm -f bsnexus-app-branch-test
    docker exec bsnexus-postgres psql -U bsnexus -d bsnexus -c \\
      "DROP DATABASE bsnexus_branch_test;"

The script re-uses the prod storageState's BSVibe-Auth-signed JWT
(both backends point at auth.bsvibe.dev so the same audience claim
verifies). No browser — captures every API response as JSON in
``frontend/test-results/branch-stage4/`` for inspection.

The branch test stack uses a fresh DB (``bsnexus_branch_test``), so
the test runs in isolation from production data on the same
PostgreSQL server. Network isolation: the container joins
``bsnexus-network`` to reach ``bsnexus-postgres`` and ``bsnexus-redis``,
and reaches ollama on the host via Tailscale magicDNS
(``http://bsserver:11434`` — see ``docker-tailscale-magicdns-no-extrahosts``
skill).

What this gate catches that ci.yml + integration tests cannot:
- Prod-build site-packages layout shadow bugs (e.g. ``backend/src/mcp/``
  vs PyPI ``mcp``)
- Real LLM dispatch + real DB write + real response_model round-trip
- Run-state-machine end-to-end (no patching of ``_on_run_completed``)
- Docker network resolution (Tailscale magicDNS to the host's ollama)

Pairs with:
- ``ci.yml`` (editable install, unit + schema)
- ``backend/tests/test_run_dispatch_integration.py`` (round-trip but
  with stubbed ``acompletion``)
- ``.github/workflows/prod-build-smoke.yml`` (prod build import
  smoke + ``create_app``, no DB, no LLM)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
import urllib.request

API = "http://127.0.0.1:8200"
ARTIFACTS = Path("/Users/blasin/Works/BSNexus/wt/feat-bsgateway-client/frontend/test-results/branch-stage4")
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def _read_jwt() -> str:
    storage_path = Path(
        "/Users/blasin/Works/BSNexus/wt/feat-bsgateway-client/frontend/e2e/.auth/prod-test-user.json"
    )
    data = json.loads(storage_path.read_text())
    for origin in data.get("origins", []):
        if origin["origin"] == "https://nexus.bsvibe.dev":
            for entry in origin.get("localStorage", []):
                if entry["name"] == "bsnexus_access_token":
                    return entry["value"]
    raise SystemExit("no JWT in storageState")


def _req(method: str, path: str, jwt: str, body: dict | None = None, name: str = "") -> tuple[int, dict | str]:
    url = f"{API}{path}"
    data = None
    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        status = e.code

    try:
        parsed: dict | str = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw

    if name:
        (ARTIFACTS / f"{name}.json").write_text(json.dumps({"status": status, "body": parsed}, indent=2))
    return status, parsed


def main() -> None:
    jwt = _read_jwt()
    print(f"[1/9] JWT loaded ({len(jwt)} chars)")

    # 2. Provision the ollama executor on the test stack
    body = {
        "name": "ollama-qwen3-coder-30b",
        "executor_type": "llm_api",
        "config": {"model": "ollama/qwen3-coder:30b", "base_url": "http://bsserver:11434"},
        "api_key": "ollama",
        "is_selected": True,
    }
    status, exec_resp = _req("POST", "/api/v1/executor-configs", jwt, body, "01-provision-executor")
    assert status == 201, f"provision failed: {status} {exec_resp}"
    assert isinstance(exec_resp, dict)
    print(f"[2/9] Executor provisioned: {exec_resp['id']}")

    # 3. Create a project
    status, proj_resp = _req(
        "POST", "/api/v1/projects", jwt,
        {"name": f"branch-stage4-{int(time.time())}", "description": "Stage 4-full local"},
        "02-create-project",
    )
    assert status == 201, f"project create failed: {status} {proj_resp}"
    assert isinstance(proj_resp, dict)
    project_id = proj_resp["id"]
    print(f"[3/9] Project created: {project_id}")

    # 4. Send chat message
    status, msg_resp = _req(
        "POST", f"/api/v1/projects/{project_id}/messages", jwt,
        {"content": "Reply with the single word PONG."},
        "03-send-message",
    )
    assert status == 201, f"send failed: {status} {msg_resp}"
    assert isinstance(msg_resp, dict)
    request_id = msg_resp["request_id"]
    print(f"[4/9] Message sent → request {request_id}")

    # 5. Poll runs until done (or 5 min timeout)
    deadline = time.time() + 300
    last_state = "unknown"
    runs: list[dict] = []
    while time.time() < deadline:
        status, runs_resp = _req(
            "GET", f"/api/v1/requests/{request_id}/runs", jwt, None, "04-poll-runs",
        )
        assert status == 200, f"GET /runs failed: {status} {runs_resp}"
        assert isinstance(runs_resp, list)
        runs = runs_resp
        if runs:
            last_state = runs[0]["status"]
            if last_state in ("done", "blocked"):
                break
        print(f"  ...{len(runs)} runs, head state={last_state}")
        time.sleep(3)

    print(f"[5/9] Final state={last_state}, runs={len(runs)}")
    assert last_state == "done", f"run did not finish cleanly: {runs}"

    # 6. Schema sanity — output_ref must be dict
    head = runs[0]
    assert isinstance(head["output_ref"], dict), f"Bug 3 regression: {head}"
    inline = head["output_ref"].get("inline", "")
    print(f"[6/9] output_ref dict-shaped, inline={inline[:80]!r}")
    assert "PONG" in inline.upper(), f"ollama didn't say PONG: {inline}"

    # 7. Loop sanity — exactly 1 run for this request
    assert len(runs) == 1, f"Bug 4 regression: {len(runs)} runs (expected 1)"
    print("[7/9] Single-run invariant holds (no spawn loop)")

    # 8. Deliverable visible
    status, deliv_resp = _req(
        "GET", f"/api/v1/projects/{project_id}/deliverables", jwt, None, "05-deliverables",
    )
    assert status == 200, deliv_resp
    print(f"[8/9] Deliverables: {len(deliv_resp) if isinstance(deliv_resp, list) else '?'}")

    # 9. Cleanup
    _req("DELETE", f"/api/v1/projects/{project_id}", jwt, None, "06-delete-project")
    _req("DELETE", f"/api/v1/executor-configs/{exec_resp['id']}", jwt, None, "07-delete-executor")
    print("[9/9] Cleaned up. Stage 4-full GREEN against branch test stack.")


if __name__ == "__main__":
    main()
