#!/usr/bin/env bash
# Infrastructure watchdog — monitors Docker/colima + vLLM, auto-recovers both.
#
# Usage: ./scripts/infra-watchdog.sh &
#
# Combines colima recovery + vLLM watchdog into one loop.
# Designed to keep the entire stack alive during long-term tests.
set -euo pipefail

VLLM_PORT="${VLLM_PORT:-8888}"
VLLM_MODEL="${VLLM_MODEL:-mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit}"
VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3-coder}"
VLLM_BIN="${VLLM_BIN:-$HOME/.venvs/vllm-mlx/bin/vllm-mlx}"
BACKEND_PORT="${BACKEND_PORT:-18100}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"
VLLM_FAIL_THRESHOLD="${VLLM_FAIL_THRESHOLD:-3}"
LOG="/tmp/infra-watchdog.log"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [infra] $*" | tee -a "$LOG"; }

start_vllm() {
  kill -9 $(lsof -ti:"$VLLM_PORT") 2>/dev/null || true
  sleep 2
  log "Starting vLLM..."
  "$VLLM_BIN" serve "$VLLM_MODEL" \
    --host 0.0.0.0 --port "$VLLM_PORT" \
    --enable-auto-tool-choice --tool-call-parser qwen \
    --reasoning-parser qwen3 --served-model-name "$VLLM_SERVED_NAME" \
    --enable-prefix-cache > /tmp/vllm.log 2>&1 &
  sleep 30
  curl -sf --max-time 5 "http://localhost:$VLLM_PORT/health" >/dev/null 2>&1 && \
    log "vLLM healthy" || log "vLLM not ready yet"
}

recover_docker() {
  log "Docker DOWN — recovering colima..."
  colima stop 2>/dev/null || true
  colima start --cpu 8 --memory 16 2>&1 | tail -1
  sleep 5

  docker compose -f "$PROJECT_DIR/.devcontainer/docker-compose.yml" up -d 2>&1 | tail -2
  sleep 5

  # Docker CLI
  docker exec -u root bsnexus-feat-company-os-app-1 bash -c \
    "curl -fsSL https://download.docker.com/linux/static/stable/aarch64/docker-27.5.1.tgz | tar xz --strip-components=1 -C /usr/local/bin docker/docker && chmod +x /usr/local/bin/docker && chmod 666 /var/run/docker.sock" 2>/dev/null

  # Backend
  docker exec -d bsnexus-feat-company-os-app-1 bash -c "cd /workspace && \
    PYTHONPATH=/workspace REDIS_URL=redis://redis:6379 \
    DATABASE_URL='postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus' \
    E2E_TEST_TOKEN=e2e-scenario-test-token \
    E2E_TEST_USER_TENANT_ID=ab8bfb15-cb63-4068-a600-54b02b33396d \
    uv run --project backend uvicorn backend.src.main:app \
    --host 0.0.0.0 --port 8000 --reload > /tmp/backend.log 2>&1"

  # Frontend
  docker exec -d bsnexus-feat-company-os-app-1 bash -c \
    "cd /workspace/frontend && pnpm dev --host 0.0.0.0 --port 3000 > /tmp/frontend.log 2>&1"

  # Wait for backend
  local i=0
  while [ $i -lt 20 ]; do
    curl -sf --max-time 3 "http://localhost:$BACKEND_PORT/health" >/dev/null 2>&1 && break
    i=$((i + 1))
    sleep 3
  done
  log "Docker recovery complete"
}

# ── Main loop ──
trap 'log "Infra watchdog stopping"; exit 0' INT TERM
log "=== Infra watchdog started ==="

vllm_fail=0
docker_fail=0

while true; do
  sleep "$POLL_INTERVAL"

  # Check Docker/colima
  if ! docker ps >/dev/null 2>&1; then
    docker_fail=$((docker_fail + 1))
    if [ "$docker_fail" -ge 2 ]; then
      recover_docker
      docker_fail=0
      # vLLM likely died too
      vllm_fail="$VLLM_FAIL_THRESHOLD"
    fi
  else
    docker_fail=0
  fi

  # Check vLLM
  if curl -sf --max-time 5 "http://localhost:$VLLM_PORT/health" >/dev/null 2>&1; then
    [ "$vllm_fail" -gt 0 ] && log "vLLM recovered after $vllm_fail failures"
    vllm_fail=0
  else
    vllm_fail=$((vllm_fail + 1))
    if [ "$vllm_fail" -ge "$VLLM_FAIL_THRESHOLD" ]; then
      log "vLLM unhealthy ($vllm_fail failures) — restarting"
      start_vllm
      vllm_fail=0
    fi
  fi
done
