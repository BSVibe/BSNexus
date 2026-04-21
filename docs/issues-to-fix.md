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

### 27. Phase 폭발 방지 ✅ (세션 9)
- **증상**: CMO → CEO → PM 체인에서 각 active agent가 phase를 만들어 E2E 4분에 22 phases 누적
- **수정**: `agent.parent_agent_id IS NULL` (org-root)만 `[CREATE_PHASE]` 실행 허용
  - `_execute_inline_markers`에 `is_org_root` 파라미터 추가 → root가 아니면 phase 마커 드롭
  - `ACTIVE_MODE_RULES_SUBORDINATE` 프롬프트로 분리: 하위 agent는 task만 생성
  - Bootstrap 예외: 프로젝트에 phase가 0개면 subordinate도 첫 phase 하나는 만들 수 있음
- **Auto-bootstrap 안전망**: Qwen3이 bootstrap 프롬프트를 무시하고 CREATE_TASK만 보내는 경우,
  phase가 없으면 backend가 자동으로 "기획" phase를 생성하여 task가 붙을 수 있게 함 (`phase_auto_bootstrapped` 로그)
- **결과**: 35.6분 longrun E2E에서 phase 6개만 생성 (<= 10 달성)

### 28. Active queue 과부하 ✅ (세션 9)
- **증상**: Auto-delegation이 @mention 받은 agent를 무조건 active로 enqueue → 큐 8-9 깊이
- **수정**: `_should_skip_active_delegation` 헬퍼
  - 대상 agent에게 pending 상태 task가 있으면 active enqueue 생략
  - Global dispatcher가 ≤5초 내 passive로 처리하므로 redundant
  - Active auto-delegation + passive ping-pong 양쪽에 적용
- **로그**: `auto_delegation_skipped_dispatcher_will_handle reason=pending_tasks_exist`
- **결과**: longrun에서 5회 이상 skip, 큐 size 3 이하 유지

### 29. 채팅 실시간 스트리밍 ✅ (세션 9)
- **수정**: `_call_agent`에서 `message_id = uuid.uuid4()` 사전 생성 → `text_delta` 이벤트 payload에 포함
  - `ConversationRepository.append`에 `message_id` 옵션 추가 → 영속 row와 id 일치
  - `_publish_tool_event`: `text_delta`일 때 `message_id`/`agent_id`/`agent_name` enrich
  - 프론트 `useChatEvents`: `text_delta` 리스너가 placeholder 메시지 upsert, 델타마다 content 누적
  - `message_created` 도착 시 authoritative row가 placeholder 교체
- **Out of scope**: Worker executor 경로 미포함 (issue #26 참조)

### 30. Phase 중복 생성 방지 ✅ (세션 9)
- **증상**: "시장 조사" / "시장 조사 및 아이디어 도출" 같은 유사 phase가 17개 누적 (longrun E2E)
- **수정**: `_find_duplicate_phase` fuzzy matcher — normalized exact + substring(≥4 chars) + SequenceMatcher≥0.75. Hangul 보존.

### 31. 빈 Phase 방지 ✅ (세션 9)
- **증상**: "통합 테스트 및 배포 준비" 같은 phase가 task 0개로 plan tree에 영구 남음 (CEO가 phase만 만들고 task는 다음 turn으로 미룸)
- **수정**: `_execute_inline_markers`에서 (a) phase 마커 per-turn cap 1개, (b) 같은 turn에 task 마커 없으면 phase 생성 스킵

### 32. Agent status idle 회귀 ✅ (세션 9)
- **증상**: 작업 중인데 status bar가 모두 idle
- **원인**: `useChatEvents.onerror`가 SSE 끊김 시 `clearAll()` — 모든 green dot 날아감. 재연결 후 다음 `started` 이벤트까지 idle
- **수정**: `clearAll()` 제거 + `agent.dot === 'green'` 백엔드 신호를 신뢰(store 비어도 green), `/agents` refetchInterval 30→10s

### 33. file_read 제거 유지 — Local LLM tool-loop 재확인 ✅ (세션 9)
- **증상**: file_read를 active mode에 복원하자 Qwen3/GLM 둘 다 `file_read×7~10` loop 후 텍스트 응답 없음
- **수정**: active mode에서 file_read도 제거. 대신 `assemble_system_prompt`가 `build_project_context(project)` 결과를 "## Current Plan State" 섹션으로 system prompt에 **inline 주입**. Passive에만 file_read.
- **추가**: `_build_chat_context`가 매 턴 `refresh_context` 호출 → `.bsnexus/context/*.md` 최신 유지 (passive file_read용)

### 34. GLM-4.7-flash 전환 ✅ (세션 9)
- **Model**: tenant default executor_config를 `ollama/glm-4.7-flash`로 변경
- **think=false**: `litellm_executor.py`에서 glm-4/deepseek-r/qwq 계열이면 `extra_kwargs["think"]=False` 자동 주입 (안 하면 reasoning 필드로 가고 content 빈 문자열)
- **timeout 상향**: `LLM_REQUEST_TIMEOUT=600→1200` (passive iteration이 길어서)
- **assignee @ prefix strip**: GLM이 `@CEO` 넣어 `_resolve_agent_by_name` fail → 파서에서 strip

### 35. 프로젝트 "종료" 정의 부재 → DONE (세션 10) — loop-breaker + artifact gate
- **인프라**:
  - `[SET_GOAL]` 블록 마커 → DB Goal(level="project") upsert. role-agnostic
  - 매 턴 `## Project Goal` 섹션으로 system prompt inline
  - `[PROJECT_COMPLETE summary="..."]` 인라인 마커 + Project.status=completed + SSE
  - Dispatcher guard 2중 (list_active_projects 필터 + advance_phase / auto_dispatch 에 early-return)
  - `build_project_context` 에 phase.description Scope + all-completed 힌트
  - ACTIVE_MODE_RULES + SUBORDINATE 양쪽 카운터룰
  - `_auto_dispatch_phase_planning` next_phase=None 분기 — **per-criterion checklist directive**
- **v3 실패 → v4 해결 경과**:
  - v3 (50min): naive "pick one of two" directive. CEO 가 phase 1개만 끝낸 상태에서 PROJECT_COMPLETE emit (premature). escape hatch.
  - v4 (33min): per-criterion checklist directive 로 교체. Goal 기준 ✅/❌ 평가 의무화.
- **v4 longrun 결과 — 정확한 종료 경로**:
  | 시점 | 상태 | CEO 평가 | 행동 |
  | --- | --- | --- | --- |
  | 12min | phase 1 '기획' 완료 | ❌ 앱 소스코드 / ❌ 디자인 화면 | CREATE_PHASE "최종 검증 및 배포" |
  | 33min | phase 2 완료 | 모든 기준 ✅ | `[PROJECT_COMPLETE summary=...]` emit |
  - DB `Project.status = completed`, auto-chain 재발동 없음
  - **핵심 검증**: 동일한 상황에서 v3 는 거짓 ✅ 찍고 종료했으나 v4 는 ❌ 찍고 phase 추가. Goal 기준 실제 평가가 강제됨.
- **그러나 v4 2번째 평가는 또 다른 false positive**:
  - 최종 산출물 실측: `schema.sql` (2KB PostgreSQL 스키마) + `deploy.sh` + `.github/workflows/deploy.yml` + 4 `.bsd` screen stub (모두 `generated_code: null`, `spec.type: "Screen"` 만 있음) + docs 3개
  - 실행 가능한 앱 코드 0 (`.js`, `.ts`, `package.json` 없음)
  - CEO 가 task.status=done 을 ✅ 근거로 사용 — 실제 파일 존재 체크 하지 않음
  - 예: `✅ 실제 동작하는 앱 소스코드 (task: '앱 기능 구현 및 화면 개발')` — 그 task 가 done 이라는 이유만으로 ✅ 찍음. 파일 확인 안 함.
- **loop-breaker + gate 둘 다 DONE**:
  - auto-chain 무한 loop 끊어짐 (directive prompt 가 마커 강제)
  - Artifact gate 가 Goal 키워드 (`소스코드`, `디자인 화면`) 대응 파일 존재 검증 — 없으면 PROJECT_COMPLETE 거부. `.bsnexus/context/*.md` 같은 메타 파일은 증거로 인정 안 함, 빈 `.bsd` stub 도 무효
  - Rejection 시 org-root 에 피드백 메시지 re-enqueue → silent stall 방지
- **남은 작은 이슈 → #37** (세션 11): Rejection 후 CEO 가 기획 phase 만 rehash 하는 경향 — 이건 구현/planning 의사결정 프롬프트 층이지, 종료 판정 메커니즘의 결함은 아님.

### 35-next. Checklist 근거 강화 → DONE (세션 10)
- **구현 (commit 15-16)**:
  - `backend/src/core/goal_verification.py` — Goal description 키워드 스캔 (code/design family) → workspace 파일 존재 검증. 빈 `.bsd` stub 제외.
  - `_execute_inline_markers` 가 PROJECT_COMPLETE 수신 시 `verify_goal_artifacts` 호출. missing 있으면 status 플립 거부 + `project_complete_rejected_missing_artifacts` 로그
  - Rejection 발생 시 org-root 에게 피드백 메시지 re-enqueue: "거부됨, 누락 항목 X 를 위한 CREATE_PHASE 필요, `.bsnexus/context/*.md` 는 증거 아님" → dispatcher 가 자체적으로 재호출 안 하는 상태에서도 CEO loop 재개
  - Directive prompt 강화: ✅ 근거로 **파일 경로 + 크기** 요구, task 이름 금지
- **v5 검증**: CEO 가 `.bsnexus/context/*.md` 를 "소스코드 증거" 로 거짓 제시 → gate reject → 그러나 재호출 없어서 stall. **수정됨 (commit 16)**.
- **v6 검증**: Rejection → redispatch → CEO 가 CREATE_PHASE 로 응답 (loop 끊김 확인) → 그러나 새 phase 도 다시 기획 phase (구현 phase 아님).

### 37. CEO 가 rejection 후에도 구현 phase 대신 기획 phase rehash (세션 11)
- **증상 (v6 longrun, project 729e0a44)**:
  - Gate 2회 reject 했음에도 CEO 가 "기획 상세화" → "기획 및 기술 스택 선정" 같은 planning phase 연속 엶
  - 모든 task 를 self (CEO) 에 assign ("do NOT assign to yourself" 룰 무시)
  - Workers 는 CEO-assigned task 에 대해 .md 문서만 생성. 실제 `.ts/.js/.py` 파일 0건
  - Gate → reject → redispatch → 또 기획 phase → ... (logical loop 은 아님, 진짜 산출물 부재 상태에서 무한 planning)
- **해결 방향 (세션 11)**:
  - A. Redispatch 메시지에 "기획 phase 금지, assignee 는 Backend_Engineer/Frontend_Engineer/Designer 중에서 선택 필수, 파일 생성 task 3-5개" 엄격 명시
  - B. N회 연속 rejection 후 human-in-loop 자동 호출 ("@user 진행 안 되는 중입니다, 확인 바랍니다")
  - C. Worker-side: task title 이 "구현"/"개발" 포함할 때 실제 code 파일 산출 템플릿 강제 (worker prompt 엔지니어링)

### 36. markers_leaked 카운팅 — longrun spec 버그 (세션 10)
- **증상**: longrun-marker-scenario.spec.ts 가 `markerLeakCount++` 를 매 poll iteration * 매 message 반복 → 같은 leaky msg 가 200+ 로 부풀려짐
- **해결**: `const seenLeakIds = new Set<string>()` 추가해 msg.id 당 1회만 카운트. 세션 11 cleanup.
- **영향**: 실제 marker leak 은 0건이지만 리포트는 216 로 찍혀 false alarm.

### 36. markers_leaked 카운팅 — longrun spec 버그 (세션 10)
- **증상**: longrun-marker-scenario.spec.ts 가 `markerLeakCount++` 를 매 poll iteration * 매 message 반복 → 같은 leaky msg 가 200+ 로 부풀려짐
- **해결**: `const seenLeakIds = new Set<string>()` 추가해 msg.id 당 1회만 카운트. 세션 11 cleanup.
- **영향**: 실제 marker leak 은 0건이지만 리포트는 216 로 찍혀 false alarm.
