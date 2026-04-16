#!/usr/bin/env bash
# Long-term stability test — runs repeated scenario cycles with vLLM watchdog.
#
# Usage:
#   ./scripts/longterm-test.sh [--hours 8] [--parallel 1]
#
# Runs scenario-test cycles back-to-back, logs results to /tmp/longterm-test/.
# Each cycle: clean DB → send chat → monitor 15min → dump results → next.
# Designed to run 8-10 hours unattended to validate:
#   - vLLM watchdog auto-recovery
#   - Task duplicate prevention under repeated runs
#   - CMO self-assign guard consistency
#   - Overall pipeline stability
set -euo pipefail

# ── Configuration ──
HOURS="${1:-8}"
VLLM_PARALLEL="${2:-1}"
MAX_CYCLES=$((HOURS * 4))  # ~15min per cycle
API="http://localhost:18100/api/v1"
TOKEN="e2e-scenario-test-token"
AUTH="Authorization: Bearer $TOKEN"
CT="Content-Type: application/json"
LOG_DIR="/tmp/longterm-test"
SUMMARY_FILE="$LOG_DIR/summary.csv"
CYCLE_TIMEOUT=900  # 15min per cycle max

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [longterm] $*" | tee -a "$LOG_DIR/main.log"; }

# ── Pre-flight checks ──
check_service() {
  local name=$1 url=$2
  curl -sf --max-time 5 "$url" >/dev/null 2>&1 && return 0
  log "ERROR: $name not reachable at $url"
  return 1
}

recover_infra() {
  # Recover colima + devcontainer if Docker is down (vLLM OOM crash)
  if docker ps >/dev/null 2>&1; then
    return 0
  fi
  log "INFRA RECOVERY: Docker is down, restarting colima..."
  colima stop 2>/dev/null
  colima start --cpu 8 --memory 16 2>&1 | tail -2
  sleep 5

  # Restart devcontainer
  local project_dir
  project_dir="$(cd "$(dirname "$0")/.." && pwd)"
  docker compose -f "$project_dir/.devcontainer/docker-compose.yml" up -d 2>&1 | tail -3
  sleep 5

  # Install docker CLI in container
  docker exec -u root bsnexus-feat-company-os-app-1 bash -c \
    "curl -fsSL https://download.docker.com/linux/static/stable/aarch64/docker-27.5.1.tgz | tar xz --strip-components=1 -C /usr/local/bin docker/docker && chmod +x /usr/local/bin/docker && chmod 666 /var/run/docker.sock" 2>/dev/null

  # Restart backend
  docker exec -d bsnexus-feat-company-os-app-1 bash -c "cd /workspace && \
    PYTHONPATH=/workspace REDIS_URL=redis://redis:6379 \
    DATABASE_URL='postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus' \
    E2E_TEST_TOKEN=e2e-scenario-test-token \
    E2E_TEST_USER_TENANT_ID=ab8bfb15-cb63-4068-a600-54b02b33396d \
    uv run --project backend uvicorn backend.src.main:app \
    --host 0.0.0.0 --port 8000 --reload > /tmp/backend.log 2>&1"

  wait_for_service "Backend" "$API/agents" 60 || { log "INFRA RECOVERY FAILED: backend not ready"; return 1; }

  # Restart vLLM watchdog if it died with colima
  if ! pgrep -f "vllm-watchdog" >/dev/null 2>&1; then
    log "INFRA RECOVERY: restarting vLLM watchdog..."
    local script_dir
    script_dir="$(cd "$(dirname "$0")" && pwd)"
    nohup "$script_dir/vllm-watchdog.sh" >> /tmp/vllm-watchdog.log 2>&1 &
    sleep 40  # Wait for watchdog to start vLLM
  fi

  wait_for_service "vLLM" "http://localhost:8888/health" 90 || log "WARNING: vLLM not ready yet, watchdog will retry"
  log "INFRA RECOVERY: complete"
}

wait_for_service() {
  local name=$1 url=$2 timeout=${3:-60}
  local start=$(date +%s)
  while true; do
    curl -sf --max-time 5 "$url" >/dev/null 2>&1 && return 0
    local elapsed=$(( $(date +%s) - start ))
    if [ "$elapsed" -ge "$timeout" ]; then
      log "TIMEOUT: $name not ready after ${timeout}s"
      return 1
    fi
    sleep 3
  done
}

clean_db() {
  docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
    "DELETE FROM conversation_messages; DELETE FROM task_activities; DELETE FROM task_history; DELETE FROM tasks; DELETE FROM phases; DELETE FROM projects;" \
    >/dev/null 2>&1
}

# ── Single cycle ──
run_cycle() {
  local cycle=$1
  local cycle_log="$LOG_DIR/cycle-${cycle}.log"
  local start_time=$(date +%s)
  log "=== CYCLE $cycle START ==="

  # Recover infrastructure if needed (colima OOM from vLLM)
  recover_infra || { log "CYCLE $cycle: infra recovery failed"; echo "$cycle,INFRA_FAIL,0,0,0,0,0,0,0," >> "$SUMMARY_FILE"; return 1; }

  # Clean DB
  clean_db || { log "CYCLE $cycle: DB clean failed"; return 1; }

  # Wait for vLLM before sending chat
  wait_for_service "vLLM" "http://localhost:8888/health" 90 || log "WARNING: vLLM not ready, proceeding anyway"

  # Create project
  local project_json
  project_json=$(curl -sf --max-time 15 -X POST "$API/projects" \
    -H "$AUTH" -H "$CT" \
    -d "{\"name\":\"longterm-cycle-${cycle}-$(date +%H%M%S)\",\"description\":\"longterm test cycle ${cycle}\",\"workspace_type\":\"server_managed\"}" 2>/dev/null)
  local project_id
  project_id=$(echo "$project_json" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
  if [ -z "$project_id" ] || [ "$project_id" = "" ]; then
    log "CYCLE $cycle: Project creation failed"
    echo "$cycle,FAIL,0,0,0,0,0,0,project_creation_failed" >> "$SUMMARY_FILE"
    return 1
  fi
  log "  Project: $project_id"

  # Send chat
  curl -sf --max-time 15 -X POST "$API/projects/$project_id/chat" \
    -H "$AUTH" -H "$CT" \
    -d '{"message":"@CMO 간단한 할 일 관리 웹앱을 만들어줘. 디자인, 백엔드, 프론트엔드 각각 팀원에게 위임해."}' \
    >/dev/null 2>&1 || { log "CYCLE $cycle: Chat send failed"; echo "$cycle,FAIL,0,0,0,0,0,0,chat_send_failed" >> "$SUMMARY_FILE"; return 1; }

  # Monitor for up to 15min
  local tasks=0 phases=0 msgs=0 files=0 screens=0 agents="" duplicates=0 self_assigns=0
  local zero_count=0
  for i in $(seq 1 90); do  # 90 * 10s = 15min
    sleep 10

    # Check Docker/colima health mid-cycle
    if ! docker ps >/dev/null 2>&1; then
      log "  [${i}0s] Docker DOWN mid-cycle — recovering..."
      recover_infra || { log "  mid-cycle recovery failed"; break; }
      zero_count=0
    fi

    # Check vLLM health (watchdog should handle restarts)
    if ! curl -sf --max-time 5 http://localhost:8888/health >/dev/null 2>&1; then
      # Only log every 60s to reduce noise
      [ $((i % 6)) -eq 0 ] && log "  [${i}0s] vLLM unhealthy — watchdog should recover"
    fi

    # Plan tree
    local plan
    plan=$(curl -sf --max-time 10 "$API/projects/$project_id/plan-tree" -H "$AUTH" 2>/dev/null || echo '{}')
    phases=$(echo "$plan" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('phases',[])))" 2>/dev/null || echo 0)
    tasks=$(echo "$plan" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(len(p.get('tasks',[])) for p in d.get('phases',[])))" 2>/dev/null || echo 0)

    # Chat messages
    local chat
    chat=$(curl -sf --max-time 10 "$API/projects/$project_id/chat" -H "$AUTH" 2>/dev/null || echo '{"messages":[]}')
    msgs=$(echo "$chat" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([m for m in d.get('messages',[]) if m.get('role')=='assistant']))" 2>/dev/null || echo 0)
    agents=$(echo "$chat" | python3 -c "import sys,json; d=json.load(sys.stdin); print(','.join(sorted(set(m.get('agent_name','?') for m in d.get('messages',[]) if m.get('role')=='assistant' and m.get('agent_name')))))" 2>/dev/null || echo "")

    # Files + screens
    files=$(curl -sf --max-time 10 "$API/projects/$project_id/files" -H "$AUTH" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); f=d if isinstance(d,list) else d.get('files',[]); print(len(f))" 2>/dev/null || echo 0)
    screens=$(curl -sf --max-time 10 "$API/projects/$project_id/design/screens" -H "$AUTH" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('screens',d if isinstance(d,list) else [])))" 2>/dev/null || echo 0)

    # Duplicate check
    duplicates=$(echo "$plan" | python3 -c "
import sys,json
d=json.load(sys.stdin)
titles=[]
for p in d.get('phases',[]):
  for t in p.get('tasks',[]):
    titles.append(t['title'].strip().lower())
seen=set()
dups=0
for t in titles:
  if t in seen: dups+=1
  seen.add(t)
print(dups)
" 2>/dev/null || echo 0)

    # Self-assign check
    self_assigns=$(echo "$plan" | python3 -c "
import sys,json
d=json.load(sys.stdin)
count=0
for p in d.get('phases',[]):
  for t in p.get('tasks',[]):
    creator=(t.get('agent_name') or '').lower()
    assigned=(t.get('assigned_agent_name') or '').lower()
    if creator and assigned and creator==assigned:
      count+=1
print(count)
" 2>/dev/null || echo 0)

    # Track consecutive zero-data responses (infra down indicator)
    if [ "$phases" -eq 0 ] && [ "$tasks" -eq 0 ] && [ "$msgs" -eq 0 ] && [ "$i" -gt 12 ]; then
      zero_count=$((zero_count + 1))
      if [ "$zero_count" -ge 6 ]; then  # 60s of zeros after initial 120s
        log "  [${i}0s] API returning zeros for 60s — breaking cycle"
        break
      fi
    else
      zero_count=0
    fi

    # Log progress every 60s
    if [ $((i % 6)) -eq 0 ]; then
      log "  [${i}0s] phases:$phases tasks:$tasks msgs:$msgs files:$files screens:$screens dups:$duplicates self:$self_assigns agents:[$agents]"
    fi

    # Early success: code files ≥3 or screens ≥1
    local code_count
    code_count=$(curl -sf --max-time 10 "$API/projects/$project_id/files?recursive=true" -H "$AUTH" 2>/dev/null | python3 -c "
import sys,json
d=json.load(sys.stdin)
files=d.get('files',d if isinstance(d,list) else [])
exts={'.py','.ts','.tsx','.js','.jsx','.html','.css'}
print(len([f for f in files if any((f if isinstance(f,str) else f.get('name','')).endswith(e) for e in exts)]))
" 2>/dev/null || echo 0)
    if [ "$code_count" -ge 3 ] || [ "$screens" -ge 1 ]; then
      log "  EARLY SUCCESS at ${i}0s: code=$code_count screens=$screens"
      break
    fi
  done

  local elapsed=$(( $(date +%s) - start_time ))
  local status="OK"
  [ "$tasks" -eq 0 ] && status="NO_TASKS"
  [ "$duplicates" -gt 0 ] && status="HAS_DUPLICATES"
  [ "$self_assigns" -gt 0 ] && status="HAS_SELF_ASSIGN"

  log "=== CYCLE $cycle END: ${elapsed}s status=$status phases=$phases tasks=$tasks dups=$duplicates self=$self_assigns files=$files screens=$screens agents=[$agents] ==="
  echo "$cycle,$status,$phases,$tasks,$duplicates,$self_assigns,$files,$screens,$elapsed,$agents" >> "$SUMMARY_FILE"
}

# ── Main ──
log "=========================================="
log "LONG-TERM TEST: ${HOURS}h, max $MAX_CYCLES cycles"
log "=========================================="

# CSV header
echo "cycle,status,phases,tasks,duplicates,self_assigns,files,screens,elapsed_s,agents" > "$SUMMARY_FILE"

# Pre-flight
check_service "Backend" "$API/agents" || exit 1
check_service "vLLM" "http://localhost:8888/health" || { log "WARNING: vLLM not running — watchdog should start it"; }

total_start=$(date +%s)
passed=0
failed=0

for cycle in $(seq 1 "$MAX_CYCLES"); do
  # Check time limit
  elapsed_total=$(( $(date +%s) - total_start ))
  max_seconds=$((HOURS * 3600))
  if [ "$elapsed_total" -ge "$max_seconds" ]; then
    log "TIME LIMIT reached (${HOURS}h). Stopping."
    break
  fi

  run_cycle "$cycle" && passed=$((passed + 1)) || failed=$((failed + 1))

  # Brief pause between cycles to let vLLM cool down
  sleep 30
done

# ── Final report ──
log ""
log "=========================================="
log "LONG-TERM TEST COMPLETE"
log "=========================================="
log "Duration: $(( ($(date +%s) - total_start) / 60 ))min"
log "Cycles: $((passed + failed)) (passed=$passed failed=$failed)"
log ""
log "=== PER-CYCLE SUMMARY ==="
cat "$SUMMARY_FILE" | tee -a "$LOG_DIR/main.log"
log ""
log "=== AGGREGATE ==="
python3 -c "
import csv, sys
rows = list(csv.DictReader(open('$SUMMARY_FILE')))
if not rows:
    print('  No data')
    sys.exit()
total = len(rows)
ok = sum(1 for r in rows if r['status'] == 'OK')
total_tasks = sum(int(r['tasks']) for r in rows)
total_dups = sum(int(r['duplicates']) for r in rows)
total_self = sum(int(r['self_assigns']) for r in rows)
total_files = sum(int(r['files']) for r in rows)
total_screens = sum(int(r['screens']) for r in rows)
avg_tasks = total_tasks / total if total else 0
print(f'  Cycles: {total} (OK: {ok}, Failed: {total-ok})')
print(f'  Avg tasks/cycle: {avg_tasks:.1f}')
print(f'  Total duplicates: {total_dups}')
print(f'  Total self-assigns: {total_self}')
print(f'  Total files: {total_files}')
print(f'  Total screens: {total_screens}')
if total_dups > 0:
    print(f'  WARNING: {total_dups} duplicate tasks detected!')
if total_self > 0:
    print(f'  WARNING: {total_self} self-assigns detected!')
" 2>/dev/null | tee -a "$LOG_DIR/main.log"
log ""
log "Full logs: $LOG_DIR/"
log "Summary CSV: $SUMMARY_FILE"
