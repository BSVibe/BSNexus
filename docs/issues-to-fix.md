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

### C1. @mention chain 미작동 ★★★
- **증상**: CMO가 `@CTO @CPO @Product_Manager` 멘션했지만 해당 에이전트들 응답 없음
- **원인**: 에이전트 응답 텍스트에 `@mention`이 있어도 chain dispatch가 안 됨.
  `chat_with_agent()`는 유저 메시지의 @mention만 파싱하고, 에이전트 응답의 @mention은 처리하지 않음.
  `_process_agent_in_background()`에서 응답 내 @mention을 파싱해서 추가 dispatch 해야 함.
- **영향**: 전체 delegation chain의 핵심. CMO→CTO→Engineer 체인이 완전히 작동하지 않음.

### C2. assigned_to 빈 값 — auto-assignment 실패
- **증상**: CMO가 create_task할 때 `assigned_to` 필드가 빈 값
- **원인**: (1) CMO가 assignee 파라미터를 안 넣거나 (2) self-assign guard에 의해 리셋되거나
  (3) `match_agent_for_task()` 키워드 매칭 실패
- **영향**: assigned_agent_id가 NULL이면 `_dispatch_agent_tasks()`가 스킵 → passive dispatch 안 됨

### C3. 자연어 입력 → CEO/CMO chain 미작동
- **증상**: `할 일 관리 웹앱 만들어줘`만 보내면 아무 반응 없거나 org-root만 실행됨.
  테스트에서 `@CMO ... 팀원에게 위임해`로 직접 지시해야만 작동.
- **원인**: `_route_via_worker()` 또는 `_find_org_root()` fallback이 올바른 에이전트를 찾지만,
  해당 에이전트가 자연어 지시만으로 full delegation chain을 자발적으로 시작하지 않음.
  Qwen3 모델의 한계 + 프롬프트 부족.
- **해결**: CEO/org-root의 system_prompt에 자연어 요청 시 자동으로 phase/task/delegation 시작 지침 추가.

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

### 15. Passive mode 에이전트가 영어로 응답
- 현재: "user가 한국어로 쓰면 한국어로 응답" 규칙 (harness.py)
- 문제: passive mode의 user_message는 시스템 생성 텍스트 + 영어 task context
  → 에이전트가 영어로 인식 → 영어 응답
- 해결: `project.language` 또는 `tenant.preferred_language` 필드 추가
