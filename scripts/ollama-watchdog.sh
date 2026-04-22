#!/usr/bin/env bash
# Ollama watchdog — monitors health and auto-restarts on failure.
#
# Usage:
#   ./scripts/ollama-watchdog.sh
#
# The script:
#   1. Starts Ollama if not already running
#   2. Checks /v1/models every POLL_INTERVAL seconds
#   3. After FAIL_THRESHOLD consecutive failures, kills and restarts Ollama
#   4. Logs all events to /tmp/ollama-watchdog.log
set -euo pipefail

# ── Configuration (override via env) ──
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"
POLL_INTERVAL="${POLL_INTERVAL:-30}"
FAIL_THRESHOLD="${FAIL_THRESHOLD:-3}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-10}"
STARTUP_WAIT="${STARTUP_WAIT:-15}"
LOG_FILE="/tmp/ollama-watchdog.log"
OLLAMA_LOG="/tmp/ollama.log"

# ── Helpers ──
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [ollama-watchdog] $*" | tee -a "$LOG_FILE"; }

get_ollama_pids() {
  lsof -ti:"$OLLAMA_PORT" 2>/dev/null || true
}

kill_ollama() {
  local pids
  pids=$(get_ollama_pids)
  if [ -n "$pids" ]; then
    log "Killing Ollama (PIDs: $pids)"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 2
  fi
}

start_ollama() {
  log "Starting Ollama: port=$OLLAMA_PORT parallel=$OLLAMA_NUM_PARALLEL"
  OLLAMA_NUM_PARALLEL="$OLLAMA_NUM_PARALLEL" \
    ollama serve > "$OLLAMA_LOG" 2>&1 &
  local pid=$!
  log "Ollama started (PID: $pid), waiting ${STARTUP_WAIT}s for warmup..."
  sleep "$STARTUP_WAIT"

  # Verify it actually started
  if ! kill -0 "$pid" 2>/dev/null; then
    log "ERROR: Ollama process died during startup. Check $OLLAMA_LOG"
    return 1
  fi

  # Wait for health (up to 60s)
  local attempts=0
  while [ $attempts -lt 12 ]; do
    if check_health; then
      log "Ollama healthy after $((STARTUP_WAIT + attempts * 5))s"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 5
  done
  log "WARNING: Ollama started but health check not passing yet"
  return 0
}

check_health() {
  curl -sf --max-time "$HEALTH_TIMEOUT" "http://localhost:$OLLAMA_PORT/v1/models" >/dev/null 2>&1
}

# ── Main loop ──
trap 'log "Watchdog stopping"; exit 0' INT TERM

log "=== Ollama watchdog started ==="
log "Config: port=$OLLAMA_PORT parallel=$OLLAMA_NUM_PARALLEL poll=${POLL_INTERVAL}s threshold=$FAIL_THRESHOLD"

# Start Ollama if not running
if ! check_health; then
  kill_ollama
  start_ollama || { log "FATAL: Cannot start Ollama"; exit 1; }
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
      log "=== RESTARTING Ollama (restart #$restart_count) ==="
      kill_ollama
      start_ollama || log "ERROR: Restart failed, will retry next cycle"
      fail_count=0
    fi
  fi
done
