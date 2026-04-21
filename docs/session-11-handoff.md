# Session 11 Handoff — #37 Verification Enforcement (prompt 한계 확정)

브랜치: `feat/plan-view-overhaul`
이전 세션 마지막 커밋: 플랜트리 API `agent_name` 수정 + 문서 정리
테스트 상태: **1098 passing** (기존 1057 + 세션 10 신규 41)

## 세션 10 성과 (27 commits)

**인프라 5층 (모두 작동 확인)**:
1. **Loop-breaker**: `[SET_GOAL]` DB upsert + `## Project Goal` 매턴 inline + `[PROJECT_COMPLETE]` marker + dispatcher guard + directive prompt
2. **Artifact gate** (`goal_verification.py`): Goal keyword → workspace 파일 존재 검증. `.bsnexus/*.md` 메타 / 빈 `.bsd` stub 제외
3. **Redispatch**: 거짓 PROJECT_COMPLETE 수신 시 org-root 재enqueue (silent stall 방지)
4. **ShellExecTool**: subprocess 기반 real exec, workspace cwd, stdout/stderr 10KB cap, timeout 60s, exit_code/timed_out 반환. Passive mode universal
5. **PASSIVE_MODE_RULES CoT**: Q1/Q2/Q3 (성공조건 → E2E 테스트 방법 → 작업). file_read as primary verify 금지 (실행 가능 산출물 한정)

**부가 수정**:
- plan-tree API `agent_name` 필드를 creator → assignee 기반 (fallback creator) — session 10 진단 중 발견한 side-effect

## V11 실측 결과 (종합 데이터)

사용자 지적대로 측정 너무 일찍 끊지 않고 DB 직접 쿼리:

| 지표 | 값 | 의미 |
|---|---|---|
| Tasks assignees | Designer 8, CTO 4, Marketer 3, Backend_Engineer 1, Frontend_Engineer 1, QA_Lead 1, CPO 1, PM 1, CEO 1, unassigned 4 | **Routing 정상** — 다양한 agent 배정됨 |
| `file_read` | 22 회 | Passive workers 실제 동작 (v8=2, v9=13, v10=6) |
| `file_write` | 5 회 | 정상 |
| `create_screen` | 1 회 | Designer 동작 |
| **`shell_exec`** | **0 회** | GLM-4.7-flash 가 자발 선택 안 함 |
| Agents appearing in chat | CEO, CMO, Designer, Frontend_Engineer, QA_Lead | Multiple agents active |

**결론**: 세션 10 의 3회 prompt 실험 (v8 direct / v9 CoT / v10 E2E) 은 유효했음. Routing 은 정상 작동, passive workers 도 능동적으로 tool 사용. 단지 **`shell_exec` 만 자발 선택 안 됨** — GLM 의 inherent tool preference limit 확정.

## 세션 11 최우선 과제

### #38 — Claude Code executor cross-check (model vs prompt 본질 판별)

**V12 (3.7h full run) 결정적 데이터**:
- 5 phases · 47 tasks done · 11 agents (CMO/CPO/PM/CEO/FE/QA_Lead/CTO/QA/Designer/Marketer/BE)
- 실제 산출물 4개 (`App.tsx`, `Layout.tsx`, `schema.sql`, `market-analysis.md`)
- CEO 체크리스트에서 ❌ 정확히 판정 (prompt 의미 이해 OK)
- **shell_exec: 0** (3.7 시간 내내)

이는 **GLM-4.7-flash 의 tool preference 한계** 시사. 단, model vs prompt 본질의 결정적 판별은 다른 model 로 cross-check 필요.

**절차**:
```bash
# Claude CLI 이미 container 에 있음 (v2.1.109)
# executor_configs tenant default 변경
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "UPDATE executor_configs SET executor_type='claude_code' WHERE is_default=true AND tenant_id='ab8bfb15-cb63-4068-a600-54b02b33396d';"
# Auth 설정 확인 (.claude/ 폴더 mount 또는 env var)
# Longrun 재실행
```

**판별 matrix**:
| Result | 결론 |
| --- | --- |
| shell_exec > 0 | GLM 한계 확정 → #37 backend enforcement 로 |
| shell_exec = 0 | Prompt 본질적 결함 → Q1/Q2/Q3 재설계 필요 |

### #37 — COMPLETE_TASK evidence enforcement (backend-level, role-agnostic)

**근거**: Prompt 층 4회 시도 (direct / CoT / E2E framing / full 3.7h run) 모두 실패. #38 cross-check 결과에 따라 두 갈래:
- GLM 한계 확정 시 → enforcement 가 유일한 경로
- Prompt 본질 결함 시 → prompt 재설계 후에도 enforcement 병행 검토

**구현 방향**:
1. `_execute_inline_markers` 의 `[COMPLETE_TASK]` 처리 시점:
   - 해당 agent turn 의 `result.tool_calls_made` 목록 확인
   - `shell_exec` / `file_read` (read-only 허용 type) 호출 여부 체크
   - 없으면 → status=done 거부, 재dispatch (message: "verification tool 호출 없이 complete 불가. shell_exec 또는 file_read 로 증거 생성 필요")
2. Task type 별 허용 verify tool mapping:
   - 실행 가능 (code, API, schema, config, .bsd) → shell_exec 필수
   - 실행 불가 (docs, 분석, 마케팅) → file_read + 가능하면 shell_exec grep
3. Tests:
   - `test_complete_task_without_verify_rejected` — COMPLETE_TASK 마커만 있고 tool_calls_made 비면 reject
   - `test_complete_task_with_shell_exec_accepted` — shell_exec 있으면 pass
   - `test_complete_task_with_file_read_accepted_for_docs_task` — docs task 는 file_read 만으로 OK

## 차순위

- #26 Worker executor streaming/budget — 이월
- CMO 가 assignee 를 CREATE_TASK 마커에 명시하도록 프롬프트 강화 (현재는 auto keyword routing 으로 커버되지만 명시적이면 더 안정)
- Schema validation 실패 retry 가 Designer 에서 자주 발생 — error msg 표준화

## 시나리오 테스트 절차

### Backend 재시작 (PID 선별 kill)
```bash
docker exec bsnexus-feat-company-os-app-1 bash -c "pgrep -f 'uvicorn backend' | xargs -r kill; \
  find /workspace/backend -name '*.pyc' -delete; \
  find /workspace/backend -name '__pycache__' -type d -exec rm -rf {} +"

JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlMmUtdGVzdC11c2VyIiwiZW1haWwiOiJlMmVAYnNuZXh1cy50ZXN0IiwiZXhwIjo5OTk5OTk5OTk5LCJhcHBfbWV0YWRhdGEiOnsidGVuYW50X2lkIjoiYWI4YmZiMTUtY2I2My00MDY4LWE2MDAtNTRiMDJiMzMzOTZkIiwicm9sZSI6ImFkbWluIn19.e2e-fake-signature"

docker exec -d bsnexus-feat-company-os-app-1 bash -c "cd /workspace && \
  PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1 \
  REDIS_URL=redis://redis:6379 \
  DATABASE_URL='postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus' \
  E2E_TEST_TOKEN='$JWT' E2E_TEST_USER_TENANT_ID=ab8bfb15-cb63-4068-a600-54b02b33396d \
  LLM_REQUEST_TIMEOUT=1200 OLLAMA_NUM_CTX=40960 \
  uv run --project backend python -B -m uvicorn backend.src.main:app \
  --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1"

sleep 20 && curl -sf http://localhost:18100/health/llm
```

### Longrun + 핵심 측정
```bash
# shell_exec uptake (enforcement 후 > 0 되어야 함)
docker exec bsnexus-feat-company-os-app-1 bash -c "grep -c shell_exec_ran /tmp/backend.log"

# Task assignee 분포 (routing 정상성)
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "SELECT a.name, COUNT(*) FROM tasks t LEFT JOIN agents a ON t.assigned_agent_id=a.id \
   WHERE t.project_id='<PID>' GROUP BY a.name ORDER BY 2 DESC;"

# Completion flow
docker exec bsnexus-feat-company-os-app-1 bash -c \
  "grep -E 'project_completed_via_marker|project_complete_rejected_missing_artifacts|complete_task_rejected_no_evidence' /tmp/backend.log | tail -10"
```

## 세션 10 에서 배운 것 (세션 11 이 실수 반복 안 하도록)

1. **측정 중단 신중히** — CHAIN_COMPLETE / STALL_FAIL 까지 기다리고 `agents=[]` snapshot 만 보지 말 것 (polling 타이밍 이슈). DB 직접 쿼리가 확실.
2. **실험 유효성 먼저 확인** — "X tool 호출 0" 이면 "X 가 필요한 경로가 도는지" 부터 체크.
3. **Prompt engineering 한계 수용** — 3회 prompt 실험 모두 shell_exec uptake 0. 추가 prompt 시도보다 backend enforcement 가 효율적.
4. **Docker pkill 주의** — `pgrep -f ... | xargs -r kill` 로 PID 1 보호.
5. **Phase count cap 금지** — 세션 9 사용자 명시.

## Open issues 요약

- **#35**: PARTIAL — 인프라 5층 완성, compliance (shell_exec uptake) 는 prompt 로 불가. #37 필요.
- **#37**: COMPLETE_TASK evidence enforcement — 세션 11 최우선.
- **#26**: Worker executor streaming/budget — 이월.
