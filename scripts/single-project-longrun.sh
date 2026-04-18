#!/usr/bin/env bash
# Single-project long-run observation.
#
# Creates ONE project, sends ONE chat message, then observes how the
# agent chain progresses over time. No new projects, no stop/restart,
# no cycle churn — just measure how deep a single scenario gets.
#
# Writes a snapshot every SNAPSHOT_INTERVAL seconds to a CSV-style log
# plus a human-readable line to stdout. Exits when:
#   - DURATION_HOURS elapses, OR
#   - no progress (phases/tasks/files) for STALL_MIN minutes, OR
#   - the project has no pending/running tasks for IDLE_MIN minutes
#     (= everything done, conversation ended).
set -euo pipefail

API="http://localhost:18100/api/v1"
TOKEN="${E2E_TOKEN:-e2e-scenario-test-token}"
AUTH="Authorization: Bearer $TOKEN"
CT="Content-Type: application/json"
PG="bsnexus-feat-company-os-postgres-1"

DURATION_HOURS="${DURATION_HOURS:-8}"
SNAPSHOT_INTERVAL="${SNAPSHOT_INTERVAL:-60}"   # every 1 min
STALL_MIN="${STALL_MIN:-30}"                    # 30m no progress = likely done
IDLE_MIN="${IDLE_MIN:-20}"                      # 20m no active tasks = done

PROJECT_NAME="longrun-$(date +%Y%m%d-%H%M)"
MESSAGE="${MESSAGE:-@CMO 시장조사해서 괜찮은 프로젝트 찾아서 보고해줘. 기획 > 개발 및 디자인 > 마케팅 수립까지 자율적으로 진행해줘.}"
LOG_DIR="/tmp/longrun-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"
CSV="$LOG_DIR/snapshots.csv"
LOG="$LOG_DIR/run.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [longrun] $*" | tee -a "$LOG"; }

psql_q() { docker exec "$PG" psql -U bsnexus -d bsnexus -t -A -F '|' -c "$1"; }

# ── 1. Create project + send chat ──
log "Creating project '$PROJECT_NAME'"
PROJECT_JSON=$(curl -sf -X POST "$API/projects" \
  -H "$AUTH" -H "$CT" \
  -d "{\"name\":\"$PROJECT_NAME\",\"description\":\"single-project longrun observation\",\"workspace_type\":\"server_managed\"}")
PID=$(echo "$PROJECT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
log "Project ID: $PID"
log "Log dir:    $LOG_DIR"
echo "$PID" > "$LOG_DIR/project-id.txt"

log "Sending chat: '$MESSAGE'"
curl -sf -X POST "$API/projects/$PID/chat" \
  -H "$AUTH" -H "$CT" \
  -d "{\"message\":\"$MESSAGE\"}" >/dev/null

# ── 2. CSV header ──
echo "timestamp,elapsed_min,phases,phases_done,tasks,tasks_done,tasks_running,tasks_pending,msgs,files,screens,stall_min" > "$CSV"

start_ts=$(date +%s)
last_progress_ts=$start_ts
last_phases=0
last_tasks=0
last_msgs=0
last_files=0
last_screens=0
idle_streak=0

# ── 3. Observation loop ──
while true; do
  now_ts=$(date +%s)
  elapsed_min=$(( (now_ts - start_ts) / 60 ))

  # Stop if duration exceeded
  if [ "$elapsed_min" -ge $((DURATION_HOURS * 60)) ]; then
    log "Duration $DURATION_HOURS h reached. Stopping."
    break
  fi

  # Pull snapshot from DB
  snap=$(psql_q "
    SELECT
      (SELECT count(*) FROM phases WHERE project_id='$PID'),
      (SELECT count(*) FROM phases WHERE project_id='$PID' AND status='completed'),
      (SELECT count(*) FROM tasks t JOIN phases ph ON t.phase_id=ph.id WHERE ph.project_id='$PID'),
      (SELECT count(*) FROM tasks t JOIN phases ph ON t.phase_id=ph.id WHERE ph.project_id='$PID' AND t.status='done'),
      (SELECT count(*) FROM tasks t JOIN phases ph ON t.phase_id=ph.id WHERE ph.project_id='$PID' AND t.status='running'),
      (SELECT count(*) FROM tasks t JOIN phases ph ON t.phase_id=ph.id WHERE ph.project_id='$PID' AND t.status='pending'),
      (SELECT count(*) FROM conversation_messages WHERE project_id='$PID')
  ")
  IFS='|' read -r phases phases_done tasks tasks_done tasks_running tasks_pending msgs <<< "$snap"

  # File counts via API (workspace listings)
  files=$(curl -sf -H "$AUTH" "$API/projects/$PID/files?recursive=true" 2>/dev/null | \
    python3 -c "
import sys, json
try:
  d = json.load(sys.stdin)
  files = d if isinstance(d, list) else d.get('files', [])
  print(sum(1 for f in files if not f.get('is_dir')))
except: print(0)
" 2>/dev/null || echo 0)
  screens=$(curl -sf -H "$AUTH" "$API/projects/$PID/design/screens" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else 0)" 2>/dev/null || echo 0)

  # Stall detection — did anything change?
  if [ "$phases" != "$last_phases" ] || [ "$tasks" != "$last_tasks" ] || \
     [ "$msgs" != "$last_msgs" ] || [ "$files" != "$last_files" ] || [ "$screens" != "$last_screens" ]; then
    last_progress_ts=$now_ts
    last_phases=$phases; last_tasks=$tasks; last_msgs=$msgs
    last_files=$files; last_screens=$screens
  fi
  stall_min=$(( (now_ts - last_progress_ts) / 60 ))

  # Idle streak (no running + no pending tasks)
  if [ "${tasks_running:-0}" = "0" ] && [ "${tasks_pending:-0}" = "0" ] && [ "${tasks:-0}" -gt 0 ]; then
    idle_streak=$((idle_streak + SNAPSHOT_INTERVAL / 60))
  else
    idle_streak=0
  fi

  ts=$(date '+%Y-%m-%d %H:%M:%S')
  echo "$ts,$elapsed_min,$phases,$phases_done,$tasks,$tasks_done,$tasks_running,$tasks_pending,$msgs,$files,$screens,$stall_min" >> "$CSV"

  log "t+${elapsed_min}m | phases $phases_done/$phases | tasks $tasks_done/$tasks (run=$tasks_running pend=$tasks_pending) | msgs=$msgs files=$files screens=$screens | stall=${stall_min}m"

  # Natural completion: fully done + idle
  if [ "$idle_streak" -ge "$IDLE_MIN" ]; then
    log "All tasks quiescent for ${IDLE_MIN}m (tasks=$tasks done=$tasks_done). Natural completion."
    break
  fi

  # Stall: nothing moved for STALL_MIN
  if [ "$stall_min" -ge "$STALL_MIN" ]; then
    log "No progress for ${STALL_MIN}m — giving up observation."
    break
  fi

  sleep "$SNAPSHOT_INTERVAL"
done

log "=== FINAL ==="
log "Project ID:  $PID"
log "URL:         http://localhost:13100/projects/$PID"
log "Phases:      $last_phases"
log "Tasks:       $last_tasks (done=$tasks_done, running=$tasks_running, pending=$tasks_pending)"
log "Messages:    $last_msgs"
log "Files:       $last_files"
log "Screens:     $last_screens"
log "Snapshots:   $CSV"
