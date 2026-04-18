#!/usr/bin/env bash
# Session 6 longterm test monitor — checks health every 30 min.
# Logs to /tmp/session6-monitor.log
set -euo pipefail

LOG="/tmp/session6-monitor.log"
INTERVAL=1800  # 30 minutes
API="http://localhost:18100/api/v1"
TOKEN="e2e-scenario-test-token"
AUTH="Authorization: Bearer $TOKEN"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [monitor] $*" | tee -a "$LOG"; }

check_count=0

log "=== Session 6 monitor started ==="

while true; do
  check_count=$((check_count + 1))
  log "--- Check #$check_count ---"

  # 1. Ollama health
  if curl -sf --max-time 5 "http://localhost:11434/v1/models" >/dev/null 2>&1; then
    log "Ollama: healthy"
  else
    log "Ollama: DOWN"
  fi

  # 2. Backend health
  llm_health=$(curl -sf --max-time 5 "http://localhost:18100/health/llm" 2>/dev/null || echo '{"status":"unreachable"}')
  log "Backend /health/llm: $llm_health"

  # 3. Docker containers
  docker_count=$(docker ps --format "{{.Names}}" 2>/dev/null | grep -c "bsnexus-feat" || echo "0")
  log "Docker containers (bsnexus-feat): $docker_count"

  # 4. Ollama watchdog alive
  if pgrep -f "ollama-watchdog" >/dev/null 2>&1; then
    log "Ollama watchdog: running"
  else
    log "Ollama watchdog: DEAD — restarting..."
    cd /Users/blasin/Works/BSNexus/feat-company-os
    nohup scripts/ollama-watchdog.sh >> /tmp/ollama-watchdog.log 2>&1 &
    log "Ollama watchdog restarted (PID: $!)"
  fi

  # 5. Playwright process alive
  if pgrep -f "playwright.*longterm" >/dev/null 2>&1; then
    log "Playwright: running"
  else
    log "Playwright: FINISHED or DEAD"
    # Check result
    if [ -f /tmp/longterm-playwright.log ]; then
      tail_lines=$(tail -5 /tmp/longterm-playwright.log)
      log "Last output: $tail_lines"
    fi
  fi

  # 6. Projects created (delegation chain progress)
  project_count=$(curl -sf --max-time 10 "$API/projects" -H "$AUTH" 2>/dev/null | \
    python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data))" 2>/dev/null || echo "?")
  log "Projects in DB: $project_count"

  # 7. Tasks summary
  task_summary=$(docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -t -c \
    "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY status;" 2>/dev/null | tr -s ' ' || echo "?")
  log "Tasks: $task_summary"

  # 8. Ollama watchdog log tail
  if [ -f /tmp/ollama-watchdog.log ]; then
    watchdog_tail=$(tail -3 /tmp/ollama-watchdog.log)
    log "Watchdog tail: $watchdog_tail"
  fi

  # 9. Playwright log tail
  if [ -f /tmp/longterm-playwright.log ]; then
    pw_tail=$(tail -5 /tmp/longterm-playwright.log)
    log "Playwright tail: $pw_tail"
  fi

  log "--- Check #$check_count done ---"
  log ""

  sleep "$INTERVAL"
done
