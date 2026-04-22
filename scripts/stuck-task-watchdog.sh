#!/usr/bin/env bash
# Stuck-task watchdog — auto-unstick running tasks that Qwen3 forgot to complete.
#
# Decision rule (simple + robust):
#   1. First time we see a running task is >STUCK_MIN old → requeue (pending)
#   2. If the SAME task is still stuck >STUCK_MIN later → auto-complete (done)
#      — it's been given a second chance, Qwen3 still won't call complete_task.
#
# We track retry counts via the task_history rows we insert (looking for our
# own actor='stuck-watchdog' transitions). This avoids relying on task_activities
# event_types that the state machine doesn't emit for tool usage.
#
# Runs forever, sleeps POLL_INTERVAL seconds between passes.
#
# Usage: ./scripts/stuck-task-watchdog.sh &
set -euo pipefail

POLL_INTERVAL="${POLL_INTERVAL:-60}"           # check every 1 min
STUCK_MIN="${STUCK_MIN:-10}"                    # >10 min running = stuck
PROJECT_MAX_AGE_MIN="${PROJECT_MAX_AGE_MIN:-20}"   # projects older than this get force-completed
LOG="/tmp/stuck-task-watchdog.log"
PG_CONTAINER="bsnexus-feat-company-os-postgres-1"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [stuck-wd] $*" | tee -a "$LOG"; }

psql_query() {
  # Read-only query. Failures are surfaced on stderr.
  docker exec "$PG_CONTAINER" psql -U bsnexus -d bsnexus \
    --set ON_ERROR_STOP=1 -t -A -F '|' -c "$1"
}

psql_tx() {
  # Write path — wrapped in a transaction so partial failures roll back.
  # ON_ERROR_STOP causes psql to exit non-zero on the first error inside
  # the block, so the caller can detect failure via `|| handle_error`.
  docker exec "$PG_CONTAINER" psql -U bsnexus -d bsnexus -v ON_ERROR_STOP=1 \
    -q -c "BEGIN;" -c "$1" -c "COMMIT;"
}

reap_old_projects() {
  # Force-complete every pending/running task in projects older than
  # PROJECT_MAX_AGE_MIN minutes. Prevents zombie cycles from a previous
  # Playwright cycle continuing to slam the LLM even after the cycle was
  # declared done (auto-handoff mentions keep waking agents forever).
  local affected
  affected=$(psql_query "
    WITH old_projects AS (
      SELECT id FROM projects
      WHERE created_at < (now() - interval '${PROJECT_MAX_AGE_MIN} minutes')
    ),
    to_close AS (
      SELECT t.id FROM tasks t
      JOIN phases ph ON t.phase_id = ph.id
      WHERE t.status IN ('pending', 'running')
        AND ph.project_id IN (SELECT id FROM old_projects)
    )
    SELECT COUNT(*) FROM to_close;
  ")
  if [ -z "$affected" ] || [ "$affected" = "0" ]; then
    return 0
  fi

  local out
  out=$(psql_tx "
    WITH old_projects AS (
      SELECT id FROM projects
      WHERE created_at < (now() - interval '${PROJECT_MAX_AGE_MIN} minutes')
    )
    UPDATE tasks SET status='done', completed_at=now(), updated_at=now()
    WHERE status IN ('pending', 'running')
      AND phase_id IN (SELECT id FROM phases WHERE project_id IN (SELECT id FROM old_projects));
  " 2>&1) || {
    log "ERROR reap_old_projects: $out"
    return 1
  }
  log "reaped ${affected} stale tasks from projects older than ${PROJECT_MAX_AGE_MIN}m"
}

tick() {
  # Find tasks stuck in `running` for >STUCK_MIN min, along with how many
  # times we've already requeued them.
  local rows
  rows=$(psql_query "
    SELECT
      t.id,
      t.project_id,
      t.title,
      COALESCE((
        SELECT COUNT(*)
        FROM task_history th
        WHERE th.task_id = t.id AND th.actor = 'stuck-watchdog' AND th.to_status = 'pending'
      ), 0) AS requeue_count
    FROM tasks t
    WHERE t.status = 'running'
      AND t.updated_at < (now() - interval '${STUCK_MIN} minutes');
  ")

  if [ -z "$rows" ]; then
    return 0
  fi

  local fixed_done=0
  local requeued=0

  while IFS='|' read -r task_id project_id title requeue_count; do
    [ -z "$task_id" ] && continue
    # Second time stuck → give up and mark done so phase_auto_chain can move.
    if [ "${requeue_count:-0}" -ge 1 ]; then
      local out
      out=$(psql_tx "
        INSERT INTO task_history (id, task_id, from_status, to_status, actor, reason, timestamp)
        VALUES (gen_random_uuid(), '${task_id}', 'running', 'done', 'stuck-watchdog',
                'auto-completed after repeated stucks (requeue_count=${requeue_count})', now());
        INSERT INTO task_activities (id, task_id, project_id, level, event_type, summary, created_at)
        VALUES (gen_random_uuid(), '${task_id}', '${project_id}', 'milestone', 'task_completed',
                'Auto-completed by stuck watchdog — agent would not close task out', now());
        UPDATE tasks SET status='done', completed_at=now(), updated_at=now() WHERE id='${task_id}';
      " 2>&1) || {
        log "ERROR auto_done task=${task_id:0:8}: $out"
        continue
      }
      log "auto_done task=${task_id:0:8} title='${title:0:60}' requeues=${requeue_count}"
      fixed_done=$((fixed_done + 1))
    else
      # First time stuck → requeue, give the dispatcher another shot.
      local out
      out=$(psql_tx "
        INSERT INTO task_history (id, task_id, from_status, to_status, actor, reason, timestamp)
        VALUES (gen_random_uuid(), '${task_id}', 'running', 'pending', 'stuck-watchdog',
                'requeued after ${STUCK_MIN}m stuck (first retry)', now());
        UPDATE tasks SET status='pending', started_at=NULL, updated_at=now() WHERE id='${task_id}';
      " 2>&1) || {
        log "ERROR requeue task=${task_id:0:8}: $out"
        continue
      }
      log "requeued task=${task_id:0:8} title='${title:0:60}' (first retry)"
      requeued=$((requeued + 1))
    fi
  done <<< "$rows"

  if [ "$fixed_done" -gt 0 ] || [ "$requeued" -gt 0 ]; then
    log "pass complete: done=$fixed_done requeued=$requeued"
  fi
}

trap 'log "stopping"; exit 0' INT TERM

log "=== stuck-task watchdog started (poll=${POLL_INTERVAL}s threshold=${STUCK_MIN}m) ==="

while true; do
  reap_old_projects || true
  tick || true
  sleep "$POLL_INTERVAL"
done
