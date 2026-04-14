#!/usr/bin/env bash
# API-based scenario test: CMO-initiated project → monitor chain → check files/design
set -euo pipefail

API="http://localhost:18100/api/v1"
TOKEN="e2e-scenario-test-token"
AUTH="Authorization: Bearer $TOKEN"
CT="Content-Type: application/json"

# ── 1. Create project ──
echo "=== Step 1: Create project ==="
PROJECT=$(curl -s -X POST "$API/projects" \
  -H "$AUTH" -H "$CT" \
  -d '{"name":"E2E Scenario '"$(date +%H:%M)"'","description":"API scenario test","workspace_type":"server_managed"}')
PROJECT_ID=$(echo "$PROJECT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Project: $PROJECT_ID"

# ── 2. Send message to CMO ──
echo ""
echo "=== Step 2: Send chat to CMO ==="
CHAT_RESP=$(curl -s -X POST "$API/projects/$PROJECT_ID/chat" \
  -H "$AUTH" -H "$CT" \
  -d '{"message":"@CMO 간단한 할 일 관리 웹앱을 만들어줘. 시장 조사하고 팀원들에게 업무 나눠서 실제 구현까지 완료해줘. 코드 파일이 나와야 해."}')
echo "Chat response: $(echo "$CHAT_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id','?')[:8])" 2>/dev/null || echo "$CHAT_RESP")"

# ── 3. Monitor progress ──
echo ""
echo "=== Step 3: Monitoring (10s intervals, max 30min) ==="
LAST_PHASES=0
LAST_TASKS=0
LAST_MSGS=0

for i in $(seq 1 180); do
  sleep 10
  ELAPSED=$((i * 10))

  # Plan tree
  PLAN=$(curl -s "$API/projects/$PROJECT_ID/plan-tree" -H "$AUTH" 2>/dev/null || echo '{}')
  PHASES=$(echo "$PLAN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('phases',[])))" 2>/dev/null || echo 0)
  TASKS=$(echo "$PLAN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(len(p.get('tasks',[])) for p in d.get('phases',[])))" 2>/dev/null || echo 0)

  # Chat messages
  CHAT=$(curl -s "$API/projects/$PROJECT_ID/chat" -H "$AUTH" 2>/dev/null || echo '{"messages":[]}')
  MSGS=$(echo "$CHAT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len([m for m in d.get('messages',[]) if m.get('role')=='assistant']))" 2>/dev/null || echo 0)
  AGENTS=$(echo "$CHAT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(','.join(sorted(set(m.get('agent_name','?') for m in d.get('messages',[]) if m.get('role')=='assistant' and m.get('agent_name')))))" 2>/dev/null || echo "")

  # Files
  FILES_COUNT=$(curl -s "$API/projects/$PROJECT_ID/files" -H "$AUTH" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); f=d if isinstance(d,list) else d.get('files',[]); print(len(f))" 2>/dev/null || echo 0)

  # Design screens
  SCREENS=$(curl -s "$API/projects/$PROJECT_ID/design/screens" -H "$AUTH" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('screens',d if isinstance(d,list) else [])))" 2>/dev/null || echo 0)

  CHANGED=""
  if [ "$PHASES" != "$LAST_PHASES" ] || [ "$TASKS" != "$LAST_TASKS" ] || [ "$MSGS" != "$LAST_MSGS" ]; then
    CHANGED=" <-- CHANGED"
  fi

  echo "  [${ELAPSED}s] phases:$PHASES tasks:$TASKS msgs:$MSGS files:$FILES_COUNT design:$SCREENS agents:[$AGENTS]$CHANGED"

  LAST_PHASES=$PHASES
  LAST_TASKS=$TASKS
  LAST_MSGS=$MSGS

  # Early success: enough activity
  if [ "$PHASES" -ge 2 ] && [ "$TASKS" -ge 5 ] && [ "$FILES_COUNT" -ge 1 ]; then
    echo ""
    echo "=== EARLY SUCCESS at ${ELAPSED}s: phases=$PHASES tasks=$TASKS files=$FILES_COUNT ==="
    break
  fi

  # Timeout check at 20min — if no progress at all
  if [ "$ELAPSED" -ge 1200 ] && [ "$MSGS" -eq 0 ]; then
    echo ""
    echo "=== TIMEOUT at ${ELAPSED}s: no agent responses ==="
    break
  fi
done

# ── 4. Final dump ──
echo ""
echo "=== Final Plan Tree ==="
curl -s "$API/projects/$PROJECT_ID/plan-tree" -H "$AUTH" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for p in d.get('phases', []):
    print(f\"Phase: {p['name']} ({p['status']})\")
    for t in p.get('tasks', []):
        agent = t.get('agent_name') or t.get('assigned_agent_name') or ''
        print(f\"  Task: {t['title']} [{t['status']}] {f'<- {agent}' if agent else ''}\")
" 2>/dev/null || echo "(empty)"

echo ""
echo "=== Files ==="
curl -s "$API/projects/$PROJECT_ID/files" -H "$AUTH" | python3 -c "
import sys, json
d = json.load(sys.stdin)
files = d if isinstance(d, list) else d.get('files', [])
for f in files[:20]:
    name = f if isinstance(f, str) else f.get('name', f.get('path', str(f)))
    print(f'  {name}')
if not files: print('  (no files)')
" 2>/dev/null || echo "  (error)"

echo ""
echo "=== Design Screens ==="
curl -s "$API/projects/$PROJECT_ID/design/screens" -H "$AUTH" | python3 -c "
import sys, json
d = json.load(sys.stdin)
screens = d.get('screens', d if isinstance(d, list) else [])
for s in screens[:10]:
    print(f\"  {s.get('name', s.get('slug', str(s)[:60]))}\")
if not screens: print('  (no screens)')
" 2>/dev/null || echo "  (error)"

echo ""
echo "=== Last 10 Chat Messages ==="
curl -s "$API/projects/$PROJECT_ID/chat" -H "$AUTH" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for m in d.get('messages', [])[-10:]:
    role = m.get('role', '?')
    agent = f\" {m['agent_name']}\" if m.get('agent_name') else ''
    content = (m.get('content') or '')[:150].replace('\n', ' ')
    print(f'[{role}{agent}] {content}')
" 2>/dev/null || echo "(empty)"

echo ""
echo "=== Summary ==="
echo "Phases: $LAST_PHASES"
echo "Tasks: $LAST_TASKS"
echo "Messages: $LAST_MSGS"
echo "Files: $FILES_COUNT"
echo "Design: $SCREENS"
echo "Agents: [$AGENTS]"

# Check for code files specifically
echo ""
echo "=== Code Files Check ==="
curl -s "$API/projects/$PROJECT_ID/files" -H "$AUTH" | python3 -c "
import sys, json
d = json.load(sys.stdin)
files = d if isinstance(d, list) else d.get('files', [])
code_exts = {'.py', '.ts', '.tsx', '.js', '.jsx', '.html', '.css', '.json'}
code_files = [f for f in files if any((f if isinstance(f,str) else f.get('name','')).endswith(ext) for ext in code_exts)]
print(f'Code files: {len(code_files)}')
for f in code_files[:20]:
    name = f if isinstance(f, str) else f.get('name', f.get('path', str(f)))
    print(f'  {name}')
if not code_files: print('  (no code files found)')
" 2>/dev/null || echo "  (error)"
