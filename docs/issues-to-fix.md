# Issues to Fix (Post Active/Passive Split)

시나리오 테스트(attempt 25) 성공 후 리팩토링 시 처리할 이슈 목록.

## 시나리오 결과 (2026-04-14)

| 항목 | 상태 | 비고 |
|------|------|------|
| 프로젝트 생성 | PASS | 매번 새 프로젝트 생성 (API) |
| Phase/Task 생성 | PASS | 4 phases, 20 tasks |
| 한국어 응답 | PASS | 전 에이전트 한국어 |
| Delegation chain | PASS | 5+ agents, auto-delegation |
| Passive dispatch | PASS | GlobalDispatcher → assigned tasks |
| Files 탭 | PASS | 9개 .md (보고서, 전략, 분석) |
| Design 탭 | PASS | 5개 .bsd (home, profile, settings) |
| **코드 파일 생성** | **FAIL** | .py/.ts 등 코드 파일 없음 |
| **빌드 가능 프로젝트** | **FAIL** | 기획+디자인에서 끊김 |

## Critical — 빌드 가능 프로젝트 산출에 필요

### 1. 체인이 기획/디자인 phase에서 끊김
- **증상**: Active agent가 "시장 조사", "보고서 작성" 같은 기획 task만 생성
- **원인**: ACTIVE_MODE_RULES에 full lifecycle 계획 지시 없음
- **해결**: "기획 → 설계 → 구현 → 테스트" 4단계 phase를 한 번에 만들라는 지시 추가
- **관련 파일**: `backend/src/core/harness.py` — ACTIVE_MODE_RULES

### 2. Coding agent가 passive dispatch 안 됨
- **증상**: Backend_Engineer, Frontend_Engineer가 코드 작성 안 함
- **원인**: 구현 task 자체가 안 만들어짐 → coding agent에 assign 안 됨
- **해결**: #1 해결 시 자동 해결. PASSIVE_MODE_RULES에 coding 지시 추가도 필요

### 3. Phase 완료 후 다음 단계 자동 진행 미확인
- **증상**: 기획 phase 완료 → 구현 phase 시작 여부 미확인
- **코드**: `GlobalDispatcher._advance_phase_if_complete()` 존재하지만 실동작 미확인
- **해결**: phase completion → CEO 알림 채팅 추가 검토

## Medium — 동작하지만 개선 필요

### 4. Passive agent가 active chain에서도 호출됨
- **증상**: passive dispatch 외에도 active delegation chain으로 동일 에이전트 호출
- **원인**: `_process_agent_in_background`(active)와 `_process_agent_in_background_passive` 분리 불완전
- **해결**: active delegation에서 이미 assigned_agent_id가 있는 task의 assignee는 chain에서 제외

### 5. Task 중복 생성
- **증상**: 같은 제목의 task가 여러 개 (Content_Writer가 반복 생성)
- **원인**: 여러 에이전트가 active mode로 동시 호출 → 각자 같은 task 생성
- **해결**: `list_tasks` 결과에서 중복 체크 강화, 또는 title uniqueness per phase

### 6. `_delegation_text` 임시 속성
- **상태**: `msg._delegation_text = ...` 로 ConversationMessage에 동적 속성 추가
- **영향**: type safety 없음, 코드 가독성 저하
- **해결**: 정식 필드로 전환하거나, delegation text를 별도 반환값으로

### 7. org_context 재추가 후 GoalAlignmentService 미사용
- **상태**: `_build_org_context()` 재활성화, `GoalAlignmentService` 미사용
- **해결**: project-level goal은 `.bsnexus/context/goals.md` file_read로 충분

## Low — 리팩토링 시 처리

### 8. Structlog + SQL echo 혼재
- backend.log에 SQL echo가 structlog 출력을 압도
- `echo=False` 설정 또는 별도 로그 파일

### 9. CRITICAL_RULES_INLINE backward compat alias
- `CRITICAL_RULES_INLINE = ACTIVE_MODE_RULES` — 기존 테스트 호환용
- 테스트 정리 후 제거 가능

### 10. test_active_passive_mode.py 미작성
- 플랜에 포함된 테스트 파일 아직 미작성
- task_assignment.py 단위 테스트 필요
- GlobalDispatcher passive dispatch 테스트 필요

### 11. 중지해도 큐잉된 에이전트가 계속 실행됨
- **증상**: 프론트에서 중지 눌러도 현재 에이전트만 멈추고, 이미 dispatch된 에이전트가 이어서 실행
- **원인**: `asyncio.create_task()`로 dispatch된 background task는 CancellationToken과 별개
  - Active mode: `_process_agent_in_background`가 delegation으로 여러 agent를 `create_task` dispatch
  - Passive mode: `GlobalDispatcher._dispatch_agent_tasks`가 5초마다 새 agent dispatch
  - 중지 시 현재 executor만 cancel, 이미 큐잉된 background task는 계속 실행
- **해결 방향**:
  - `_project_tasks` dict에 있는 모든 background task를 cancel
  - GlobalDispatcher가 중지된 프로젝트의 task dispatch를 skip
  - 프로젝트 레벨 stop flag (Redis key 또는 DB status) 체크

### 12. vLLM 인프라 불안정
- 대형 context에서 hang → kill -9 재시작
- Colima crash — vLLM + Docker 동시 메모리 사용
- 장기적: vLLM 대신 ollama, 또는 더 작은 모델 검토
