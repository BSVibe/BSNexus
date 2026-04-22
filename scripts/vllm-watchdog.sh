#!/usr/bin/env bash
# vLLM watchdog — monitors health endpoint and auto-restarts on hang.
#
# Usage:
#   ./scripts/vllm-watchdog.sh [--port 8888] [--model mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit]
#
# The script:
#   1. Starts vLLM if not already running
#   2. Checks /health every POLL_INTERVAL seconds
#   3. After FAIL_THRESHOLD consecutive failures, kills and restarts vLLM
#   4. Logs all events to /tmp/vllm-watchdog.log
set -euo pipefail

# ── Configuration (override via env) ──
VLLM_PORT="${VLLM_PORT:-8888}"
VLLM_MODEL="${VLLM_MODEL:-mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit}"
VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3-coder}"
VLLM_BIN="${VLLM_BIN:-$HOME/.venvs/vllm-mlx/bin/vllm-mlx}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"       # seconds between health checks
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"      # consecutive failures before restart
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-10}"     # curl timeout for health check
STARTUP_WAIT="${STARTUP_WAIT:-30}"         # seconds to wait after starting vLLM
LOG_FILE="/tmp/vllm-watchdog.log"
VLLM_LOG="/tmp/vllm.log"

# ── Helpers ──
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] $*" | tee -a "$LOG_FILE"; }

get_vllm_pids() {
  lsof -ti:"$VLLM_PORT" 2>/dev/null || true
}

kill_vllm() {
  local pids
  pids=$(get_vllm_pids)
  if [ -n "$pids" ]; then
    log "Killing vLLM (PIDs: $pids)"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 2
  fi
}

start_vllm() {
  log "Starting vLLM: model=$VLLM_MODEL port=$VLLM_PORT"
  "$VLLM_BIN" serve "$VLLM_MODEL" \
    --host 0.0.0.0 \
    --port "$VLLM_PORT" \
    --enable-auto-tool-choice \
    --tool-call-parser qwen \
    --reasoning-parser qwen3 \
    --served-model-name "$VLLM_SERVED_NAME" \
    --enable-prefix-cache \
    > "$VLLM_LOG" 2>&1 &
  local pid=$!
  log "vLLM started (PID: $pid), waiting ${STARTUP_WAIT}s for warmup..."
  sleep "$STARTUP_WAIT"

  # Verify it actually started
  if ! kill -0 "$pid" 2>/dev/null; then
    log "ERROR: vLLM process died during startup. Check $VLLM_LOG"
    return 1
  fi

  # Wait for health endpoint (up to 60s)
  local attempts=0
  while [ $attempts -lt 12 ]; do
    if curl -sf --max-time "$HEALTH_TIMEOUT" "http://localhost:$VLLM_PORT/health" >/dev/null 2>&1; then
      log "vLLM healthy after $((STARTUP_WAIT + attempts * 5))s"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 5
  done
  log "WARNING: vLLM started but health check not passing yet"
  return 0
}

check_health() {
  curl -sf --max-time "$HEALTH_TIMEOUT" "http://localhost:$VLLM_PORT/health" >/dev/null 2>&1
}

# ── Main loop ──
trap 'log "Watchdog stopping"; exit 0' INT TERM

log "=== vLLM watchdog started ==="
log "Config: port=$VLLM_PORT model=$VLLM_MODEL poll=${POLL_INTERVAL}s threshold=$FAIL_THRESHOLD"

# Start vLLM if not running
if ! check_health; then
  kill_vllm
  start_vllm || { log "FATAL: Cannot start vLLM"; exit 1; }
fi

fail_count=0
restart_count=0

while true; do
  sleep "$POLL_INTERVAL"

  if check_health; then
    if [ "$fail_count" -gt 0 ]; then
      log "Health recovered after $fail_count failures"
    fi
    fail_count=0
  else
    fail_count=$((fail_count + 1))
    log "Health check FAILED ($fail_count/$FAIL_THRESHOLD)"

    if [ "$fail_count" -ge "$FAIL_THRESHOLD" ]; then
      restart_count=$((restart_count + 1))
      log "=== RESTARTING vLLM (restart #$restart_count) ==="
      kill_vllm
      start_vllm || log "ERROR: Restart failed, will retry next cycle"
      fail_count=0
    fi
  fi
done
