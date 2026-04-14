# Issues to Fix (Pre-Refactoring)

Phase 1 (prompt inline) 완료 후 Playwright E2E 검증에서 발견된 이슈 목록.
시나리오 테스트 성공 후 전체 리팩토링 시 함께 처리.

## 테스트 진행 상황 (2026-04-14)

| 항목 | 상태 | 비고 |
|------|------|------|
| 프로젝트 생성 (API) | PASS | 매번 새 프로젝트 생성 |
| Phase/Task 생성 | PASS | 1 phase, 4 tasks (이전 1개 → 4개) |
| 한국어 응답 | PARTIAL | 이전 테스트에서 확인, 최신 테스트에서 빈 응답 |
| 에이전트 체이닝 | FAIL | CMO만 응답, delegation chain 미발생 |
| Files 탭 | FAIL | file_write 미사용 |
| Design 탭 | FAIL | create_screen 미사용 |
| vLLM 안정성 | ISSUE | 대형 프롬프트에서 hang, 재시작 필요 |

## Critical — 시나리오 성공에 필요

### 1. 에이전트가 한국어 미준수
- **증상**: 사용자 한국어 메시지에 에이전트가 영어로 응답
- **원인 추정**: LANGUAGE 규칙이 시스템 프롬프트 상단에 있지만 Qwen3-14B가 무시
- **로그**: "CEO] @CMO, please take the..." (한국어 입력 → 영어 응답)
- **영향**: 사용자 경험 저하

### 2. 에이전트가 file_write 미사용
- **증상**: Files 탭에 에이전트가 생성한 파일 없음
- **원인 추정**: 프롬프트에 "file_write로 결과 저장" 지시가 약함, 또는 모델이 tool 선택 시 file_write를 선호하지 않음
- **확인 필요**: tool_calls 로그에서 file_write 호출 여부 확인

### 3. 에이전트가 create_screen (.bsd) 미사용
- **증상**: Design 탭에 스크린 없음
- **원인 추정**: Design skill을 가진 에이전트(Designer)에게 task가 delegate되지 않았거나, delegate되었어도 create_screen을 호출하지 않음
- **확인 필요**: Designer 에이전트가 chain에 참여했는지, design capability가 할당되었는지

## Medium — 동작하지만 개선 필요

### 4. 기존 프로젝트에서 테스트 시 이전 데이터 간섭
- **증상**: 이전 세션의 phase/task/message가 남아있어 새 메시지 결과만 분리 불가
- **해결**: 테스트 전 새 프로젝트 생성 또는 기존 데이터 정리 API 필요

### 5. dead code: _build_org_context 호출부 제거됨
- **상태**: `_build_org_context()` 함수 자체는 유지했지만, `_build_chat_context()`에서 호출하지 않음
- **영향**: org mission이 프롬프트에 미포함 (이전에는 system prompt에 주입)
- **결정 필요**: org mission을 `.bsnexus/context/goals.md`에서 file_read로 읽게 할지, 다시 inline할지

### 6. goal_context 제거로 GoalAlignmentService 미사용
- **상태**: `_build_chat_context()`에서 GoalAlignmentService 호출 제거
- **영향**: project-level goal이 프롬프트에 미포함
- **결정 필요**: `.bsnexus/context/goals.md`에 이미 refresh되므로 file_read로 충분한지

## Low — 리팩토링 시 처리

### 7. unused import 정리
- `GoalAlignmentService` import 제거 완료
- `_build_org_context` 함수 — 다른 곳에서 사용하지 않으면 제거 후보

### 8. test_chat_org_context.py 테스트 약화
- `test_system_prompt_places_org_context_before_role` → `test_system_prompt_contains_agent_identity`로 교체
- org context 관련 테스트 커버리지 감소

### 9. CRITICAL_RULES_INLINE + workspace rules 중복
- `CRITICAL_RULES_INLINE`에 workflow/delegation 규칙이 있고
- `.bsnexus/rules/response-format.md`에도 동일 내용이 있음
- 에이전트 프롬프트에 중복 삽입됨 → 토큰 낭비
