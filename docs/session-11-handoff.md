# Session 11 Handoff — Enforce PROJECT_COMPLETE emit + Longrun spec cleanup

브랜치: `feat/plan-view-overhaul`
이전 세션 마지막 커밋: `462130c fix(projects): drain agent queue on project delete + batch delete`
테스트 상태: **1080 passing** (기존 1057 + 세션 10 신규 23)

## 세션 10 성과 (7 commits, 1080 tests)

| # | 주제 | 파일 |
| --- | --- | --- |
| 1 | `[SET_GOAL]` 블록 → DB Goal upsert, `upsert_project_goal` util | agent_chat, plan_tools |
| 2 | `## Project Goal` 섹션 system prompt inline + missing warning = FIRST TURN SET_GOAL rule (role-agnostic) | harness, agent_chat |
| 3 | `[PROJECT_COMPLETE]` 인라인 마커 + dispatcher guard + ACTIVE_MODE 양쪽 카운터룰 | task_markers, agent_chat, global_dispatcher, harness |
| 4 | `build_project_context` 에 phase.description (Scope) + all-completed 힌트 | task_markers |
| 5 | 공용 `_match_phase_by_name` (4-ladder fuzzy) + `create_task_from_params` 호출 | plan_tools |
| 6 | `ACTIVE_MODE_RULES_SUBORDINATE` 에 Phase Alignment 의무 규칙 | harness |
| 7 | `DELETE /projects/{id}` + batch delete 가 `cancel_project()` 호출 | projects.py |

## Longrun 검증 (2026-04-20, 7h)

프로젝트: `6a669df0-def6-4de0-94fb-c7ca944e7da8` — "E2E Longrun 04-20 16:02"
결과: **STALL** (commits 3-7 loop-breaker 가 효과 부족)

### ✅ 작동 확인
- **CMO role-agnostic SET_GOAL** — 첫 턴에 자발적으로 `[SET_GOAL]` 블록 emit, DB 저장됨:
  ```
  title: "E2E Longrun 04-20 16:02 프로젝트 목표 설정"
  desc : "실제 동작하는 앱의 소스코드와 디자인 화면을 모두 완성하여 시장에 출시 가능한 MVP를 제공함."
  ```
- **Marker stripping** — chat 20 msgs 에 `[CREATE_TASK`/`[CREATE_PHASE` 0건
- **Delegation chain** — 12 agents 참여 (CMO/CEO/CTO/QA_Lead/Backend_Engineer/Frontend_Engineer/Designer/DevOps/QA/Engineer/Product_Manager/Content_Writer)
- **123 tasks 생성 + 123 done** — execution pipeline 정상
- **Queue drain** — 프로젝트 DELETE 시 `project_delete_queue_drained cancelled=136` 로그 확인

### ❌ 미해결 (세션 11 최우선)
- **PROJECT_COMPLETE 마커 0건 emit** — 모든 agents 가 자연어로 "프로젝트 성공적으로 종료하겠습니다" 라고만 말하고 마커는 안 씀
- **7 phases 폭발** — CEO 가 auto-chain 에서 계속 new phase 만듦 (Phase count cap 금지 사용자 명시)
- **54min stall** — CHAIN_COMPLETE 기준 (phases≥3, tasks≥15, agents≥5, doneTasks≥10, files≥5) 중 files:3 만 미달, 나머지는 오래 전에 넘음

## 세션 11 최우선 과제

### 1. PROJECT_COMPLETE emit 강제 (#35-next)

프롬프트 룰만으로는 GLM-4.7-flash 가 마커 emit 수행 안 함. 더 강한 장치 필요.

**추천 접근 C (gate-based prompt)**:
- `_auto_dispatch_phase_planning` 이 CEO 를 호출하기 전에, project 상태가 "종료 가까움" (예: 모든 phase done + goal.description 에 부합하는 산출물 있음) 이면
- **전용 종료-평가 프롬프트** 로 CEO 호출:
  ```
  아래 Goal criteria 와 현재 Plan State 를 검토해주세요.
  - criteria 가 전부 충족됐으면 한 줄로 `[PROJECT_COMPLETE summary="..."]` 만 emit
  - 아직 필요한 작업이 있으면 평소처럼 CREATE_PHASE + CREATE_TASK
  반드시 둘 중 하나를 해야 합니다.
  ```
- 응답에 PROJECT_COMPLETE 있으면 `_execute_inline_markers` 가 `Project.status=completed` 로 전환
- 없으면 일반 flow 로 복귀

**대안**:
- **D. Human-in-loop**: 10min stall 감지 시 backend 가 자동으로 user 채팅에 "완료처럼 보여요 — 맞으면 `ok` 답신해주세요" 발행, `ok` 수신 시 force complete
- **B. 단일 tool**: 매우 제한된 active-mode tool `complete_project(summary)` 하나만 노출 (Qwen3/GLM 모두 tool loop 경험 있으므로 주의)

### 2. Longrun spec 카운팅 버그 (#36)
`longrun-marker-scenario.spec.ts` 의 `markerLeakCount++` 는 매 poll 마다 재카운트 → false alarm 216. `seenLeakIds: Set<string>` 으로 msg.id 당 1회 집계.

### 3. 남은 세션 10 계획 작업
- ~~Commit 8 (docs)~~ 는 이 handoff 포함으로 진행 중
- `test_delete_project_drains_queue` 통합 테스트 (real PG + real queue) — 현재 unit mock 만 있음. 가능하면 fresh-PG integration 으로 격상.

## 차순위

- #26 Worker executor streaming/budget — 이월
- markdown 기반 Design view 검증 — 세션 9에서 중단
- `stuck-watchdog` (금지 영역) — 계속 건드리지 말 것

## 시나리오 테스트 절차

세션 10 handoff 원본 그대로 사용 (JWT 토큰 고정, Backend 기동 스크립트 동일).

### Backend 재시작

```bash
# 컨테이너가 죽지 않도록 PID 선별 kill (PID 1 은 devcontainer init shell)
docker exec bsnexus-feat-company-os-app-1 bash -c "pgrep -f 'uvicorn backend' | xargs -r kill; \
  find /workspace/backend -name '*.pyc' -delete; \
  find /workspace/backend -name '__pycache__' -type d -exec rm -rf {} +"

JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlMmUtdGVzdC11c2VyIiwiZW1haWwiOiJlMmVAYnNuZXh1cy50ZXN0IiwiZXhwIjo5OTk5OTk5OTk5LCJhcHBfbWV0YWRhdGEiOnsidGVuYW50X2lkIjoiYWI4YmZiMTUtY2I2My00MDY4LWE2MDAtNTRiMDJiMzMzOTZkIiwicm9sZSI6ImFkbWluIn19.e2e-fake-signature"

docker exec -d bsnexus-feat-company-os-app-1 bash -c "cd /workspace && \
  PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1 \
  REDIS_URL=redis://redis:6379 \
  DATABASE_URL='postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus' \
  E2E_TEST_TOKEN='$JWT' \
  E2E_TEST_USER_TENANT_ID=ab8bfb15-cb63-4068-a600-54b02b33396d \
  LLM_REQUEST_TIMEOUT=1200 OLLAMA_NUM_CTX=40960 \
  uv run --project backend python -B -m uvicorn backend.src.main:app \
  --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1"

sleep 15 && curl -sf http://localhost:18100/health/llm
```

### E2E Longrun

```bash
cd /Users/blasin/Works/BSNexus/feat-company-os/frontend && \
  LIVE_FRONTEND_URL=http://localhost:13100 LIVE_API_URL=http://localhost:18100 \
  E2E_TOKEN="$JWT" \
  pnpm exec playwright test e2e/specs/longrun-marker-scenario.spec.ts \
  --timeout=29000000 --reporter=list 2>&1 | tee /tmp/longrun-e2e.log
```

### Goal 및 status 확인

```bash
# SET_GOAL 저장 확인
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "SELECT title, left(description,120) FROM goals \
   WHERE project_id='<PROJECT_ID>' AND level='project';"

# PROJECT_COMPLETE 후 status 확인
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "SELECT name, status FROM projects WHERE id='<PROJECT_ID>';"
```

### Auto-chain 재발동 체크

```bash
docker exec bsnexus-feat-company-os-app-1 grep "phase_auto_chain_dispatched" /tmp/backend.log | \
  awk -F'project_id=' '{print $2}' | sort | uniq -c
# completed 프로젝트는 더 이상 재발동되지 않아야 함
```

## 작업 방식

- **TDD 필수** (`/feature-workflow` 스킬). 1080 passing 깨지 말 것.
- **MCP Playwright 대신 기존 spec 사용** — `longrun-marker-scenario.spec.ts` 가 이미 있음 (사용자 지적)
- **Phase count cap 같은 기계적 상한 금지** (#35 결정)
- **Active mode 에 tool 다시 추가 금지** — Qwen3/GLM 모두 loop 검증됨
- **docker exec 에서 pkill 은 PID 1 가능성 고려** — `pgrep -f ... | xargs kill` 로 surgical
