# 세션 10 위임 프롬프트

## 컨텍스트

BSNexus Company OS — AI 에이전트 org chart 기반 협업 시스템. `feat/plan-view-overhaul` 브랜치.

**세션 9 성과 (1057 tests passing, 13 commits)**:
- Phase 폭발 방지: org-root 전용 CREATE_PHASE + bootstrap 예외 + backend auto-bootstrap 안전망
- Phase 중복 fuzzy dedup (`_find_duplicate_phase`: normalized + substring + SequenceMatcher≥0.75)
- 빈 phase 방지 (phase-per-turn cap 1개 + task 동반 요구)
- Active queue throttle (`_should_skip_active_delegation` — pending task 있으면 active enqueue skip)
- Chat 실시간 스트리밍 UI (pre-allocated `message_id` → text_delta → frontend placeholder upsert)
- Agent status idle 회귀 fix (`clearAll()` 제거, `agent.dot==='green'` 신뢰, refetch 30→10s)
- **Qwen3 → GLM-4.7-flash 전환** (`think=false` 자동 주입, `LLM_REQUEST_TIMEOUT=1200`)
- Active mode tool 최종 제거 — **inline "Current Plan State" 주입**이 유일한 전략 (Qwen3/GLM 모두 tool 주면 loop)
- `refresh_context` 매 턴 호출 → passive file_read가 항상 최신 상태 조회
- Assignee `@` prefix strip (GLM이 `@CEO` 넣는 것 대응)

**longrun E2E**: 2회 CHAIN_COMPLETE success (Qwen3 35.6min, 28.5min), GLM 65min+ (phase 4까지 진행 후 "종료 선언" 못해 stall — #35로 이어짐)

**인수인계서**: `~/.claude/projects/-Users-blasin/memory/project_bsnexus_task_centric_refactor.md`

**세션 9 handoff 원본**: `docs/session-9-handoff.md`

---

## 이번 세션 최우선 과제

### 1. 프로젝트 "종료" 정의 메커니즘 ⚠️ 핵심 과제

**증상** (GLM longrun에서 재현):
- 모든 phase COMPLETED 이후에도 CEO가 `phase_auto_chain_dispatched next_phase=None`로 계속 dispatch됨 → CEO가 또 새 phase 생성 → 무한 반복
- 4 phase / 25 tasks 모두 완료됐는데 CEO가 "프로젝트 완료"로 판단 못하고 65분째 새 phase 계획 중

**근본 문제**: "프로젝트 종료"의 정의가 use case마다 다름
- "TodoApp 계획해줘" → 기획만으로 충분
- "실제 동작하는 앱 만들어" → 구현까지
- "출시까지 진행해" → 출시 단계
- "마케팅까지 해줘" → 운영/마케팅 포함

⚠️ **Phase count cap 같은 기계적 상한은 금지** (세션 9에서 사용자가 명시적으로 거부). 마케팅 포함 프로젝트는 10+ phase가 자연스러울 수 있음.

**해결 방향** (선택/조합):

- **A. 종료 조건 추출 + 체크 (추천)**:
  1. 사용자 최초 메시지가 들어오면 backend에서 LLM으로 "종료 조건"을 추출 → `Project.completion_criteria` 필드 (JSON) 저장. 예: `{"deliverables": ["앱 소스코드", "디자인 화면"], "scope": "implementation"}`.
  2. 매 CEO turn 프롬프트에 "## 종료 조건\n- 앱 소스코드 ✅ 완료\n- 디자인 화면 ⚠️ 부족" 같이 inline.
  3. CEO가 완료 판단 시 `[PROJECT_COMPLETE summary="..."]` 같은 신규 마커 emit → backend가 더 이상 auto-chain 안 함.
  - 테스트: 최초 메시지 10개 케이스로 추출 정확도 확인. 간단한 것부터 복잡한 것까지.

- **B. Human-in-loop "완료 확인" 메시지**:
  - Phase 완료 시점에 CEO가 새 phase 만들기 전 현재 상황을 요약하고 "@user 추가 요청 있으신가요?" 같은 메시지 발행.
  - 사용자 응답 없이 N분(예: 10분) 지나면 자동 종료 처리.
  - 간단한 UX, 정확한 종료 감지.

- **C. Goal Tracker** (`Goal` 모델 이미 있음):
  - `Goal.level="project"` 이 완료 criteria 역할. 사용자 최초 메시지에서 `[SET_GOAL]` 마커 생성하도록 CMO 프롬프트 유도.
  - 매 턴 프로젝트 Goals 상태 inline.

**참고 파일**:
- `backend/src/api/agent_chat.py` — `_build_chat_context`, `assemble_system_prompt` 호출 경로
- `backend/src/core/harness.py:676` `refresh_context`, `assemble_system_prompt` 의 "## Current Plan State" 섹션 생성부
- `backend/src/core/global_dispatcher.py:248-300` `_auto_dispatch_phase_planning` — auto-chain 발동 지점
- `backend/src/models/goal.py` — 기존 Goal 모델 (level: mission/department/project/task)
- `backend/src/core/task_markers.py` — 새 마커 추가 시 파서 확장

**시작 전에 구현 방향 A/B/C 중 하나 선택**. A가 가장 근본적이지만 복잡. B가 가장 단순하지만 사용자 경험 개입.

---

### 2. Task-Phase 미스매치 (issue #25)

**증상**: CEO가 phase 열고 subordinate가 task를 넣을 때 phase-task 연결이 약함. 예: "시장 조사 및 기획" phase에 "백엔드 API" task가 들어감.

**해결 방향**:
- CREATE_TASK 마커에 `phase_name` 지정 권장 프롬프트 강화
- 또는 Subordinate가 task 만들 때 **현재 active phase 컨텍스트를 더 강하게** 보여주기 (이미 inline plan state 있지만 phase description 등 추가)
- Task-phase 유사도 체크: 생성된 task의 키워드와 active phase 이름 유사도 측정 → 너무 안 맞으면 경고

---

### 3. Stale Project Queue Drain (세션 9에서 발견된 pre-existing 이슈)

**증상**: `DELETE /projects/{id}` 하면 DB는 지워지지만 agent queue에 남은 해당 프로젝트용 작업이 계속 실행됨 → FK 위반 로그 쏟아짐 + Ollama 슬롯 점유.

**해결 방향**:
- `project DELETE` API에서 `agent_queue_manager`에 해당 project_id 관련 큐 항목 drain
- `AgentQueueManager.drain_project(project_id)` 추가 — in-flight request의 project_id 매칭 시 skip

---

## 차순위 과제

4. **#26 Worker executor streaming/budget** — Claude Code worker 경로에서 streaming/usage/text_delta 미지원. 세션 9 Part 3의 out-of-scope 항목.
5. **한글 응답 품질** — Qwen3에서 영어 혼재 (GLM 전환 후 개선됨). 필요 시 Modelfile 튜닝.
6. **Phase 이름 품질** — GLM이 "GLM-4.7-flash"라는 자기 모델 이름을 phase로 쓴 케이스 관찰됨 (세션 9). 프롬프트 강화.

---

## 시나리오 테스트 절차

### Backend 재시작 (필수: `.pyc` 캐시 방지)

```bash
docker exec bsnexus-feat-company-os-app-1 bash -c "pkill -9 -f 'uvicorn backend' 2>/dev/null; \
  find /workspace/backend -name '*.pyc' -delete 2>/dev/null; \
  find /workspace/backend -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null"

JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlMmUtdGVzdC11c2VyIiwiZW1haWwiOiJlMmVAYnNuZXh1cy50ZXN0IiwiZXhwIjo5OTk5OTk5OTk5LCJhcHBfbWV0YWRhdGEiOnsidGVuYW50X2lkIjoiYWI4YmZiMTUtY2I2My00MDY4LWE2MDAtNTRiMDJiMzMzOTZkIiwicm9sZSI6ImFkbWluIn19.e2e-fake-signature"

docker exec -d bsnexus-feat-company-os-app-1 bash -c "cd /workspace && \
  PYTHONPATH=/workspace PYTHONDONTWRITEBYTECODE=1 \
  REDIS_URL=redis://redis:6379 \
  DATABASE_URL='postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus' \
  E2E_TEST_TOKEN='$JWT' \
  E2E_TEST_USER_TENANT_ID=ab8bfb15-cb63-4068-a600-54b02b33396d \
  LLM_REQUEST_TIMEOUT=1200 \
  OLLAMA_NUM_CTX=40960 \
  uv run --project backend python -B -m uvicorn backend.src.main:app \
  --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1"

sleep 15 && curl -sf http://localhost:18100/health/llm
```

### E2E Longrun

```bash
cd /Users/blasin/Works/BSNexus/feat-company-os/frontend && \
  JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...<위와_동일>..." \
  LIVE_FRONTEND_URL=http://localhost:13100 \
  LIVE_API_URL=http://localhost:18100 \
  E2E_TOKEN="$JWT" \
  pnpm exec playwright test e2e/specs/longrun-marker-scenario.spec.ts \
  --timeout=29000000 --reporter=list 2>&1 | tee /tmp/longrun-e2e.log
```

### UI 관찰 (Playwright MCP)

- URL: `http://bsserver:13100` (localhost는 BSVibe auth가 redirect 거부)
- 로그인: `admin@bsvibe.dev` / `admin1234!`
- 프로젝트 열기 후 스크린샷으로 status bar / plan tree / chat 실시간 관찰

### Monitor 패턴

```
tail -f /tmp/longrun-e2e.log | grep --line-buffered \
  -E "\[[0-9]+min\]|CHANGED|FULL_SUCCESS|CHAIN_COMPLETE|STALL_FAIL|EXIT:|FAILED|passed|failed"
```

### Debug 명령

```bash
# Redis processing keys
docker exec bsnexus-feat-company-os-redis-1 redis-cli KEYS "agent:processing:*"

# Backend structlog (SQL 제외)
docker exec bsnexus-feat-company-os-app-1 bash -c \
  "grep '\[info' /tmp/backend.log | grep -vE 'sqlalchemy|SELECT' | tail -30"

# Phase/task 상태
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "SELECT name, status, (SELECT count(*) FROM tasks WHERE phase_id=phases.id) \
   FROM phases WHERE project_id='<PROJECT_ID>' ORDER BY \"order\";"

# Project 삭제
curl -sf -X DELETE -H "Authorization: Bearer $JWT" \
  "http://localhost:18100/api/v1/projects/<PROJECT_ID>"
```

---

## 접속 정보

- Frontend: http://bsserver:13100 (localhost는 BSVibe auth 거부)
- Backend: http://localhost:18100
- Login: `admin@bsvibe.dev` / `admin1234!`
- E2E JWT token: 위 Backend 재시작 블록 참조 (고정 토큰, tenant_id `ab8bfb15-*`)
- Model: `ollama/glm-4.7-flash` (executor_configs DB default, GLM이 Qwen3보다 품질 우수)
- `think=false` 자동 주입됨 (`litellm_executor.py`에서 glm-4/deepseek-r/qwq 계열 감지)

---

## 작업 방식

- **TDD 필수** (`/feature-workflow` 스킬). 1057 passing 깨지 말 것.
- 커밋 단위: 논리 그룹별 분리
- **하지 말 것**:
  - `.bsd` canonical schema 건드리지 말기
  - stuck-watchdog 수정 안 함
  - `uvicorn --reload` 쓰지 말기 (좀비 multiprocess 원인)
  - **Active mode에 tool 다시 추가하지 말기** — Qwen3/GLM 모두 loop 검증 완료. Inline prompt만이 유일한 전략
  - **Phase count cap 같은 기계적 상한 쓰지 말기** — 사용자 명시 거부 (#35)
- **Ollama 설치 모델**:
  - `glm-4.7-flash:latest` 17GB (현재 default)
  - `qwen3-coder:30b` 17GB (fallback)
  - `qwen3:14b` 8GB, `ministral-3:14b` 8GB

---

## 시작 전 체크리스트

```bash
# 1. Ollama + 모델
curl -sf --max-time 5 http://localhost:11434/api/tags | head -5

# 2. Docker / Backend / Frontend
docker ps | grep bsnexus-feat
curl -sf http://localhost:18100/health/llm
curl -sf http://bsserver:13100 >/dev/null && echo "FE ok"

# 3. 테스트 통과 확인
cd /Users/blasin/Works/BSNexus/feat-company-os && \
  uv run --project backend pytest backend/tests/ --timeout=300 -q 2>&1 | tail -3
# 기대: 1057 passed

# 4. Default model 확인
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -t -c \
  "SELECT config FROM executor_configs WHERE is_default=true AND executor_type='generic_llm';"
# 기대: {"model": "ollama/glm-4.7-flash", ...}
```

---

## 완료 기준 (Session 10)

- [ ] **#35 프로젝트 종료 메커니즘 선택 + 구현** (A/B/C 중 하나, 또는 조합)
  - 최소: Longrun E2E에서 모든 phase 완료 후 무한 phase 생성 안 됨 + 명확한 종료 신호
- [ ] **#25 Task-Phase 미스매치 완화** (프롬프트 또는 경고)
- [ ] **Stale queue drain** — project 삭제 시 큐 정리, FK 위반 로그 사라짐
- [ ] 1057+ tests passing (regression 없음)
- [ ] Longrun E2E 한 번 성공 (CHAIN_COMPLETE + 의미 있는 종료)
- [ ] `issues-to-fix.md` 갱신 (#35 완료 표시, 새 이슈 발견 시 추가)
- [ ] 세션 10 커밋 논리 그룹별 분리
- [ ] `session-11-handoff.md` 작성 + 메모리 파일 갱신
