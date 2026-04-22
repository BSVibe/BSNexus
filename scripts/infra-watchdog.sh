#!/usr/bin/env bash
# Infrastructure watchdog — monitors Docker/colima + Ollama, auto-recovers both.
#
# Usage: ./scripts/infra-watchdog.sh &
#
# Combines colima recovery + Ollama watchdog into one loop.
# Designed to keep the entire stack alive during long-term tests.
set -euo pipefail

OLLAMA_PORT="${OLLAMA_PORT:-11434}"
OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"
BACKEND_PORT="${BACKEND_PORT:-18100}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"
OLLAMA_FAIL_THRESHOLD="${OLLAMA_FAIL_THRESHOLD:-3}"
LOG="/tmp/infra-watchdog.log"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [infra] $*" | tee -a "$LOG"; }

start_ollama() {
  kill -9 $(lsof -ti:"$OLLAMA_PORT") 2>/dev/null || true
  sleep 2
  log "Starting Ollama..."
  OLLAMA_NUM_PARALLEL="$OLLAMA_NUM_PARALLEL" ollama serve > /tmp/ollama.log 2>&1 &
  sleep 15
  curl -sf --max-time 5 "http://localhost:$OLLAMA_PORT/v1/models" >/dev/null 2>&1 && \
    log "Ollama healthy" || log "Ollama not ready yet"
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

ollama_fail=0
docker_fail=0

while true; do
  sleep "$POLL_INTERVAL"

  # Check Docker/colima
  if ! docker ps >/dev/null 2>&1; then
    docker_fail=$((docker_fail + 1))
    if [ "$docker_fail" -ge 2 ]; then
      recover_docker
      docker_fail=0
      # Ollama likely died too
      ollama_fail="$OLLAMA_FAIL_THRESHOLD"
    fi
  else
    docker_fail=0
  fi

  # Check Ollama
  if curl -sf --max-time 5 "http://localhost:$OLLAMA_PORT/v1/models" >/dev/null 2>&1; then
    [ "$ollama_fail" -gt 0 ] && log "Ollama recovered after $ollama_fail failures"
    ollama_fail=0
  else
    ollama_fail=$((ollama_fail + 1))
    if [ "$ollama_fail" -ge "$OLLAMA_FAIL_THRESHOLD" ]; then
      log "Ollama unhealthy ($ollama_fail failures) — restarting"
      start_ollama
      ollama_fail=0
    fi
  fi
done
