# Session 11 Handoff — #35 완전 해결, 정리 및 차순위

브랜치: `feat/plan-view-overhaul`
이전 세션 마지막 커밋: `65754da fix(dispatcher): force structured PROJECT_COMPLETE vs CREATE_PHASE choice when all phases done`
테스트 상태: **1080 passing**

## 세션 10 성과 (9 commits, #35 DONE)

| # | 주제 | 파일 |
| --- | --- | --- |
| 1 | `[SET_GOAL]` 블록 → DB Goal upsert, `upsert_project_goal` util | agent_chat, plan_tools |
| 2 | `## Project Goal` system prompt inline + missing warning = FIRST TURN SET_GOAL (role-agnostic) | harness, agent_chat |
| 3 | `[PROJECT_COMPLETE]` 인라인 마커 + dispatcher guard 2중 + ACTIVE_MODE 양쪽 카운터룰 | task_markers, agent_chat, global_dispatcher, harness |
| 4 | `build_project_context` 에 phase.description (Scope) + all-completed 힌트 | task_markers |
| 5 | 공용 `_match_phase_by_name` (4-ladder fuzzy) + `create_task_from_params` 호출 | plan_tools |
| 6 | `ACTIVE_MODE_RULES_SUBORDINATE` 에 Phase Alignment 의무 규칙 | harness |
| 7 | `DELETE /projects/{id}` + batch delete 가 `cancel_project()` 호출 | projects.py |
| 8 | docs (session-11 handoff + issues-to-fix + memory) | docs |
| 9 | `_auto_dispatch_phase_planning` 의 `next_phase=None` 분기를 **directive prompt** 로 교체 — loop-breaker | global_dispatcher |

## Longrun 검증 — #35 Loop 종료 확인

**프로젝트**: `91cb9e77-a193-4169-9fdd-d0d7837d1ada` / "E2E Longrun 04-21 00:09" (50분)

| 시각 | 이벤트 |
| --- | --- |
| 00:09 | CMO 가 SET_GOAL 블록 emit → DB Goal 저장 |
| 00:09~30 | Phase 1 "기획" 에서 7 tasks 생성 + 모두 done |
| 00:30 | `phase_auto_chain_dispatched next_phase=None` — **directive prompt 로 CEO 재호출** |
| 00:39 | CEO 가 `[PROJECT_COMPLETE summary="..."]` emit |
| 00:39 | `project_completed_via_marker by_agent=CEO` 백엔드 로그 |
| ✅ | `Project.status = completed` DB 확인. auto-chain loop 정상 종료 |

**이전 세션 (commit 1-8 만) 의 문제점**: GLM-4.7-flash 는 프롬프트 카운터룰을 무시하고 자연어로 "프로젝트 종료하겠습니다" 라고만 답변 → 마커가 안 emit 되어 loop 유지. **Commit 9 의 directive prompt 로 해결**. 명시적으로 "아래 2개 마커 중 하나 반드시 emit" 을 요구한 게 결정적.

## 차순위 이슈

### #25 Task-Phase 미스매치 → ~PARTIAL (세션 10)
- commit 4: phase.description Scope 인라인, commit 5: fuzzy phase_name, commit 6: Phase Alignment rule
- 세션 11 에서 longrun 으로 실측 확인 (fallback warning 로그 발생 빈도, 실제 미스매치 건수)

### #36 longrun spec markers_leaked 카운팅 버그
- `markerLeakCount++` 가 poll * msg 로 중복 증가 → 실제 leak 0 인데 216 으로 찍힘
- `seenLeakIds: Set<string>` 로 msg.id 당 1회 집계

### #26 Worker executor streaming/budget 미지원
- Claude Code worker 경로에서 streaming/usage/text_delta 없음 (세션 9 out-of-scope)

### stress-test: 여러 프로젝트 동시 진행
- `cancel_project(project_id)` unit mock 만 있음. fresh-PG integration 테스트 필요
- 동시 10 프로젝트에서 queue drain, dispatcher, goal 등 체크

### #35 파생 — 좀 더 다양한 종료 시나리오
- "출시까지" / "마케팅까지" 같은 multi-phase 프로젝트에서도 directive prompt 가 작동하는지 (세션 10 은 "기획부터 개발/디자인까지" 50분 프로젝트에서만 검증)
- longrun 테스트 prompt 조정해 출시/마케팅 포함한 장시간 시나리오 돌리기 → 5+ phases 진행 후 PROJECT_COMPLETE emit 되는지

## 시나리오 테스트 절차

### Backend 재시작 (PID 선별)
```bash
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

sleep 20 && curl -sf http://localhost:18100/health/llm
```

### Longrun
```bash
cd /Users/blasin/Works/BSNexus/feat-company-os/frontend && \
  LIVE_FRONTEND_URL=http://localhost:13100 LIVE_API_URL=http://localhost:18100 \
  E2E_TOKEN="$JWT" \
  pnpm exec playwright test e2e/specs/longrun-marker-scenario.spec.ts \
  --timeout=29000000 --reporter=list 2>&1 | tee /tmp/longrun-e2e.log
```

### #35 검증 commands
```bash
# Goal 저장
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "SELECT title, left(description,120) FROM goals WHERE project_id='<PID>' AND level='project';"

# Project 종료 확인
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "SELECT name, status FROM projects WHERE id='<PID>';"

# Loop-breaker 작동 로그
docker exec bsnexus-feat-company-os-app-1 grep -E \
  'project_completed_via_marker|phase_auto_chain_skipped_project_completed' /tmp/backend.log
```

## 작업 방식

- **TDD 필수** (`/feature-workflow` 스킬). 1080 passing 깨지 말 것.
- **MCP Playwright 대신 기존 spec 사용** — `longrun-marker-scenario.spec.ts`
- **Phase count cap 금지** — directive prompt 가 동작하므로 이제 cap 필요 없음
- **Active mode 에 tool 재추가 금지** — GLM/Qwen3 모두 loop 검증됨
- **docker exec 에서 pkill 은 surgical** — `pgrep -f ... | xargs -r kill` 로 PID 1 보호
