# Issues to Fix (Post Active/Passive Split)

시나리오 테스트 결과 기반 이슈 목록 (2026-04-15 세션 4 업데이트).

## 시나리오 결과 (2026-04-15, 세션 4)

| 항목 | 상태 | 비고 |
|------|------|------|
| 프로젝트 생성 | PASS | API + Playwright 모두 |
| Phase/Task 생성 | PASS | 2-5 phases, 6-9 tasks |
| 첫 Phase auto-active | PASS | create_phase 시 자동 active |
| Delegation chain | PASS | CMO → CTO/PM/Designer/FE/BE/QA/DevOps |
| Passive dispatch | PASS | GlobalDispatcher → claim → file_write → complete |
| Phase auto-chain | PASS | phase 완료 → CEO 자동 dispatch (unit test 확인) |
| 빈 phase guard | PASS | task 0개 phase는 advance 스킵 |
| Files API | PASS | workspace 경로 일치, 파일 목록 반환 |
| **코드 파일 생성** | **PASS** | server.js (Express REST API), package.json 등 |
| file_write → complete_task | PASS | qwen3-coder-30b에서 3+ tool call 동작 |
| **디자인 파일 (.bsd)** | **PARTIAL** | Designer가 create_screen 호출하지만 간헐적 |
| **빌드 가능 프로젝트** | **PARTIAL** | 단일 서버 파일 산출, 전체 프로젝트 구조는 미완 |
| **30분 풀 테스트** | **FAIL** | vLLM hang (~10분에 멈춤) |

## Critical — 코드 파일 산출 + 안정성

### 1. ~~에이전트별 FIFO 큐 필요~~ → DONE (세션 3)
- 에이전트별 asyncio.Queue FIFO 큐 구현 완료
- active/passive 요청 순차 처리

### 2. ~~CTO → Engineer 멘션 체인 미도달~~ → DONE (세션 4)
- Phase auto-chain + ACTIVE_MODE_RULES 개선으로 해결
- CMO → CTO/PM → Designer/FE/BE/QA/DevOps 체인 동작 확인

### 3. ~~Phase 자동 진행 미확인~~ → DONE (세션 4)
- _advance_phase_if_complete() + _auto_dispatch_phase_planning() 구현
- 빈 phase guard (task 0개면 advance 스킵)
- 첫 phase auto-active

### 4. ~~Files API 경로 불일치~~ → DONE (세션 4)
- workspace.py, design.py: `/data/workspaces` → `data/workspaces`

## Critical — 코어 dispatch chain 문제 (세션 5 롱텀 테스트 발견)

### C1. @mention chain 불안정 ★★★
- **증상**: CMO가 `@CTO @CPO @Product_Manager` 멘션했지만 해당 에이전트들 응답 없음
- **원인**: delegation chain 코드는 정상 (`_process_agent_in_background()` line 803-842에서
  `_delegation_text` → `_parse_mentions()` → enqueue). 문제는 **vLLM hang/colima crash로
  에이전트 응답 자체가 미도달**하여 delegation까지 실행되지 않는 것.
  세션 4에서 동작 확인됨 (commit 5093e52). 인프라 안정성이 근본 원인.
- **영향**: chain이 끊기면 CMO만 task 생성하고 끝남 — 다른 에이전트 passive 실행 안 됨.
- **해결**: vLLM/LLM 안정성 확보가 선행. ollama 전환 또는 API LLM 검토.

### C2. assigned_to 빈 값 — auto-assignment 실패
- **증상**: CMO가 create_task할 때 `assigned_to` 필드가 빈 값
- **원인**: (1) CMO가 assignee 파라미터를 안 넣거나 (2) self-assign guard에 의해 리셋되거나
  (3) `match_agent_for_task()` 키워드 매칭 실패
- **영향**: assigned_agent_id가 NULL이면 `_dispatch_agent_tasks()`가 스킵 → passive dispatch 안 됨

### C3. 자연어 입력 → 올바른 delegation
- **증상**: 시나리오 테스트에서 `@CMO 할 일 관리 웹앱 만들어줘. 팀원에게 위임해`로 직접 지시.
  올바른 방법: `@CMO 시장조사해서 프로젝트 제안해줘` (CMO 역할에 맞는 지시).
  또는 멘션 없이 `할 일 관리 웹앱 만들어줘`만 보내면 CEO→CMO chain이 자연스럽게 작동해야 함.
- **원인**: 현재 테스트가 CMO에게 역할과 맞지 않는 직접 지시를 보냄.
  자연어만으로 CEO가 적절한 에이전트에게 위임하려면 CEO system_prompt 개선 필요.
- **해결**: (1) 시나리오 테스트 메시지를 역할에 맞게 수정 (2) CEO system_prompt에 자연어 요청 시 delegation 지침.

### C4. 에이전트 언어 일관성 (한글 입력 → 영어 응답) ★★
- **증상**: 한글로 멘션해도 CMO가 영어로 응답
- **원인**: `assemble_system_prompt()`에 LANGUAGE 규칙 있지만 (line 312-314),
  Qwen3가 tool_use 후 응답에서 언어를 바꿈. `/no_think` prefix + 긴 영어 시스템 프롬프트 영향.
- **해결**: (1) `LANGUAGE` 규칙을 시스템 프롬프트 **끝**에 재배치 (recency bias)
  (2) project/tenant에 `preferred_language` 설정 추가
  (3) 프롬프트를 한국어로 작성 (영어 프롬프트가 영어 응답 유도)

## Medium — 동작하지만 개선 필요 (세션 5에서 수정됨: #5, #6, #7, #8, #13)

### 5. ~~Task 중복 생성~~ → DONE (세션 5)
- create_task에서 phase-scoped 중복 체크 (exact + fuzzy >0.8 SequenceMatcher)
- 롱텀 테스트 24 cycles 0 duplicates 확인
- **증상**: active 에이전트들이 같은 제목의 task를 반복 생성 (23개 중 대부분 중복)
- **원인**: CMO가 task 생성 후 @mention한 에이전트들이 active mode에서 또 같은 task 생성.
  list_tasks로 기존 task를 보지만 "내가 다시 만들어야 한다"고 판단.
- **해결 방향**:
  - A) create_task에서 같은 phase 내 제목 유사도 체크 + 거부
  - B) active mode에서 list_tasks 결과를 더 명확히 → "이미 있으니 만들지 마라"
  - C) 두 가지 조합

### 6. ~~CMO self-assign~~ → DONE (세션 5)
- create_task에서 self-assign guard + ACTIVE_MODE_RULES 프롬프트 강화
- 롱텀 테스트 24 cycles 0 self-assigns 확인

### 7. ~~vLLM hang + 병목~~ → MITIGATED (세션 5)
- vllm-watchdog.sh: 30초 간격 health check + 3회 실패 시 자동 재시작
- infra-watchdog.sh: colima crash 자동 복구 포함
- executor timeout 600s→180s + retry on timeout
- /health/llm endpoint 추가
- 근본 해결은 올라마 전환 또는 GPU 서버 필요

### 8. ~~.bsd 디자인 간헐적~~ → DONE (세션 5)
- DESIGN_TASK_RULES 상수 추가 + passive mode design agent에 자동 주입

### 9. `_delegation_text` 임시 속성
- **상태**: `msg._delegation_text = ...` 로 ConversationMessage에 동적 속성 추가
- **영향**: type safety 없음
- **해결**: 정식 필드로 전환

## Low — 리팩토링 시 처리

### 10. Structlog + SQL echo 혼재
- echo=False 또는 별도 로그 파일

### 11. CRITICAL_RULES_INLINE backward compat alias
- `CRITICAL_RULES_INLINE = ACTIVE_MODE_RULES` — 테스트 정리 후 제거

### 12. test_active_passive_mode.py 미작성
- task_assignment.py 단위 테스트 필요
- GlobalDispatcher passive dispatch 테스트 필요

### 13. ~~중지해도 큐잉된 에이전트가 계속 실행됨~~ → DONE (세션 5)
- GlobalDispatcher.pause_project() + CancellationToken + AgentQueueManager.cancel_project()
- restart endpoint (blocked→pending) + UI 중지/재시작 토글

### 14. 채팅 히스토리 20개 제한 (pagination 없음)
- `GET /chat` — `MAX_HISTORY = 20`으로 최근 20개만 반환
- 98개 메시지 중 최초 유저 메시지가 안 보임
- 프론트에서 scroll-up pagination 필요

### 15. ~~Passive mode 에이전트가 영어로 응답~~ → PARTIAL (세션 6)
- LANGUAGE 규칙을 프롬프트 끝으로 이동 (recency bias 활용)
- 남은 이슈: passive mode user_message가 영어 task context → 에이전트가 영어로 인식
- 추가 해결: `project.language` 또는 `tenant.preferred_language` 필드 추가

### 16. ~~E2E Auth 깨짐 (BSVibe Auth cookie 전환 후)~~ → DONE (세션 6)
- `injectAuth()` + `setupAuth()`에 `addInitScript` 추가 — navigation 전에 localStorage 주입
- mock-api.ts / longterm-ui-live.spec.ts 둘 다 수정

### 17. ~~Task가 done 상태로 전환되지 않음~~ → MITIGATED (세션 6)
- `complete_task` 호출 누락은 모델 한계 (Qwen3 성향)
- `scripts/stuck-task-watchdog.sh`로 자동 복구:
  - 5분+ stuck running task → pending 재dispatch (1차)
  - 같은 task 또 stuck → done 강제 마감
- 실측: 166 auto_done + 175 requeued, 0 errors

## Session 6 완료 (2026-04-17~18)

### ~~C1: Delegation chain 안정성~~ → DONE
- vLLM 180s timeout → **Ollama 전환 + LLM_REQUEST_TIMEOUT=600s env-override**
- `_process_agent_in_background_passive`에 **auto-recovery** (passive error → pending → 최대 3회 retry → blocked)
- 친화적 에러 메시지 — raw Python/LLM 에러 본문 숨김

### ~~C2: Self-assign (통계 오류)~~ → FALSE POSITIVE
- DB 감사에서 "159/163 self-assign" 발견 → 실제는 `global_dispatcher.py:359` 가 passive dispatch 시 `task.agent_id = agent.id`로 creator field를 executor로 덮어써서 통계 왜곡
- create_task 로그로 확인: creator(CEO/Marketer 등) ≠ assignee 정상
- **남은 버그**: `task.agent_id`에 creator와 executor를 같이 쓰지 말고 분리 필드 필요

### ~~C3: 자연어 dispatch~~ → DONE
- CEO/CTO 등 `system_prompt`에 자연어 브리핑 + @mention 지침
- `_summarize_actions` 대화체 템플릿 + assignee 자동 @mention
- `_looks_like_tool_call_json` guard로 raw JSON content 차단

### ~~C4: 언어 일관성~~ → DONE
- LANGUAGE 규칙을 프롬프트 끝으로 (recency bias)

## Session 6 신규 인프라

### .bsd canonical schema
- `backend/src/core/bsd_schema.py` — 19 types (Screen/View/Row/ScrollView/Text/Heading/Button/TextInput/Checkbox/Switch/Image/Icon/Badge/Card/Divider/List/ListItem/Appbar/Tab/Fab)
- 타입별 허용 props, 허용 style keys, normalise(aliases: Container→View 등), validate(strict)
- `create_screen` / `modify_screen` tool이 validation 후 reject/auto-normalise
- Designer skill prompt에 canonical vocabulary markdown 주입
- `ScreenRenderer.tsx` (Preview/JSON toggle) + full-scope 렌더러 (RN→web CSS + tokens)

### Watchdogs
- `scripts/ollama-watchdog.sh` — health check + auto-restart
- `scripts/stuck-task-watchdog.sh` — 5분+ running stuck task auto-unstick (requeue → done)
- `scripts/single-project-longrun.sh` — 1 프로젝트 8시간 자율 체이닝 관찰 스크립트

### Self-healing
- `_recover_failed_passive_task()` — passive 모드 에러(timeout/connection/rate-limit/unknown) 시 자동 retry
- `_pick_next_handoff_agent()` — passive 완료 시 @mention 없으면 자동 핸드오프 대상 선정 (같은 phase pending → 다른 phase pending → org root)

### Ping-pong delegation
- Passive 에이전트 완료 메시지의 @mention을 파싱해 **active mode로 재dispatch** — 연쇄 대화 복구
- `passive_ping_pong` 로그로 추적

## Session 6 롱텀 검증 결과 (8h single-project)

프로젝트 `dd0bce26-c358-4efb-a794-592868784646`:
- **11 phases 전부 완료** (Market Research → Product Planning → Product Dev → UI Dev → User Mgmt → Auth → CI/CD → User Profile → Monitoring → Dashboard UI/UX → Auth Security)
- 163 tasks, 136 done (83%)
- 86 files (code 28, docs 20, design 31, tests 3)
- 183 assistant messages, 11 agents 활동, 145/183 (79%) @mention 포함
- Watchdog: 166 auto_done + 175 requeued, 0 errors
- Auto-recovery: 9회 (passive error → pending → retry 성공)

## 남은 이슈 (세션 6 이후)

### ~~18. create_task 중복 guard 미작동~~ → DONE (세션 7)
- **근인**: TOCTOU race condition — 병렬 dispatched 에이전트들이 각각 새 DB 세션에서 dedup 체크하지만 PostgreSQL READ COMMITTED에서 서로의 uncommitted INSERT를 못 봄
- **수정**: PostgreSQL partial unique index `ix_tasks_phase_title_active` ON `(phase_id, lower(trim(title))) WHERE status != 'done'` + `CreateTaskTool`에서 `IntegrityError` catch → 기존 task 반환
- Alembic migration: `n6i7j8k9l0m1`

### ~~19. global_dispatcher가 `task.agent_id`를 executor로 덮어씀~~ → DONE (세션 7)
- **수정**: `agent_id` → `creator_agent_id` rename (Alembic `o7j8k9l0m1n2`)
- `global_dispatcher.py:359` (`task.agent_id = agent.id`) 삭제
- `plan_tools.py` ClaimTask에서 `task.agent_id = ctx.agent_id` 삭제
- 에이전트 실행 중 task 조회 (plan_tree, agents API)를 `assigned_agent_id` 기준으로 변경
- Backend 14개 파일 + Frontend 4개 파일 rename 완료

### ~~20. Designer가 `modify_screen` 미활용~~ → DONE (세션 7)
- **수정**: `CreateScreenTool`에서 기존 slug 존재 시 `ToolExecutionError` (modify_screen 안내) + fuzzy check (>0.7 similarity)
- DESIGN_SKILL 프롬프트에 "기존 screen 확인 후 modify_screen 사용" 규칙 추가

### ~~21. CEO @mention 후 일부 task에 assigned_agent_id=NULL~~ → DONE (세션 7)
- **근인**: LLM이 "Product_Manager"로 쓰지만 agent name은 "PM" → exact match 실패 → NULL
- **수정**: `_resolve_agent_by_name()` 3-tier matching (exact → role → starts-with)
- 실검증: 14개 task 중 null_assigned 0건

### 22. Dispatcher crash — multiple active phases
- **증상**: 병렬 create_phase 호출로 2+ phases가 active → `get_active_phase()` `scalar_one_or_none` crash
- **수정**: `order_by(Phase.order.asc()).limit(1)` — crash 방어
- **남은**: 근본적으로 active phase가 1개만 되도록 create_phase에서 guard 필요

### 23. Active mode agent busy 표시 ✅→진행중 (세션 8)
- **구현 완료**: `agent_processing` SSE event + `agentProcessingStore` (zustand) + Redis transient key
- **스트리밍 전환**: `acompletion(stream=True)` → `[STATUS ...]` 마커 실시간 감지 → SSE `status_update`
- **Project-scoped Redis key**: `agent:processing:{project_id}:{agent_id}` (멀티 프로젝트 safe)
- **Dispatch-time 즉시 발행**: POST /chat, GlobalDispatcher에서 started 발행 (background 중복 제거)
- **남은 문제**:
  - `.pyc` 캐시로 새 코드 미반영 가능 → `PYTHONDONTWRITEBYTECODE=1` + `python -B` 사용
  - Agent 탭(/agents)은 조직도 전용 — status 표시 안 함 (의도)
  - Worker executor에서 streaming/STATUS 미지원 (다음 세션)
  - 채팅 실시간 스트리밍 (ChatGPT 스타일 타이핑) — `text_delta` 이벤트 발행 중, 프론트 구현 필요

### 24. 마커 기반 전체 전환 ✅ (세션 8)
- **수정**: create_task, create_phase, claim_task, complete_task 모두 tool에서 제거 → 마커로 대체
  - `[CREATE_TASK title="..." assignee="..."]`, `[CREATE_PHASE name="..."]`
  - `[CLAIM_TASK]`, `[COMPLETE_TASK summary="..."]`
  - `[STATUS 현재 작업 상태]` — 실시간 activity text
- Active mode tools: `list_tasks`, `set_goal`, `record_decision`, `file_read`, `list_files`만 유지
- Passive mode tools: `file_write`, `file_read`, `list_files`, `list_tasks`, `create_screen`, `modify_screen`만 유지
- Done-after-done 방지: dedup이 done task도 포함
- Phase 생성 촉진 프롬프트 강화
- CoT reasoning step 추가 (passive mode)
- 1027 tests passing

### 25. Task-Phase 미스매치
- **증상**: "시장 조사 및 기획" phase에 frontend/backend 구현 task가 들어감
- **원인**: CMO가 첫 phase에 모든 task를 넣고 phase를 분리하지 않음
- **해결 방향**: 프롬프트에서 "각 phase는 하나의 작업 영역만" 규칙 강화

### 26. Worker executor에서 budget/streaming 미지원
- **증상**: Claude Code 같은 worker executor는 LiteLLM 기반이 아니라서 streaming/cost 계산 미대응
- **해결 방향**: worker 프로토콜에 streaming + usage 필드 추가, executor별 override 구조
