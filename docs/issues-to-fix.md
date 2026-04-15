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

## Medium — 동작하지만 개선 필요

### 5. Task 중복 생성 ★
- **증상**: active 에이전트들이 같은 제목의 task를 반복 생성 (23개 중 대부분 중복)
- **원인**: CMO가 task 생성 후 @mention한 에이전트들이 active mode에서 또 같은 task 생성.
  list_tasks로 기존 task를 보지만 "내가 다시 만들어야 한다"고 판단.
- **해결 방향**:
  - A) create_task에서 같은 phase 내 제목 유사도 체크 + 거부
  - B) active mode에서 list_tasks 결과를 더 명확히 → "이미 있으니 만들지 마라"
  - C) 두 가지 조합

### 6. CMO self-assign
- **증상**: CMO가 create_task에서 assignee를 자기 자신으로 설정
- **원인**: ACTIVE_MODE_RULES에 "Do NOT @mention yourself"는 있지만,
  create_task의 assignee 필드에는 제한 없음
- **해결**: create_task에서 현재 에이전트를 assignee로 설정하면 경고 또는 거부.
  또는 프롬프트에 "자기 자신을 assignee로 설정하지 마세요" 추가.

### 7. vLLM hang + 병목
- **증상**: qwen3-coder-30b가 동시 요청 시 ~10분에 hang
- **원인**: sequential inference에 여러 에이전트가 동시 요청 → 큐 적체 → hang
- **대안**:
  - ollama `OLLAMA_NUM_PARALLEL=2` (병렬 처리)
  - vLLM 재시작 cron (10분마다 health check)
  - GPU 서버 또는 API 기반 LLM으로 전환
- **참고**: qwen3-14b는 tool call 2개 제한 (claim→complete만, file_write 스킵)
  qwen3-coder-30b는 3+ tool call 가능하지만 hang 위험

### 8. .bsd 디자인 간헐적
- **증상**: Designer가 create_screen을 호출하지만 항상은 아님
- **원인**: passive 프롬프트에 "design task → create_screen" 가이드는 있지만
  Qwen3가 file_write(문서)로 대체하는 경향
- **해결**: design capability 에이전트의 passive 프롬프트에
  "UI/UX task는 반드시 create_screen으로 .bsd 파일 생성" 강화

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

### 13. 중지해도 큐잉된 에이전트가 계속 실행됨
- 프로젝트 레벨 stop flag 필요

### 14. 채팅 히스토리 20개 제한 (pagination 없음)
- `GET /chat` — `MAX_HISTORY = 20`으로 최근 20개만 반환
- 98개 메시지 중 최초 유저 메시지가 안 보임
- 프론트에서 scroll-up pagination 필요

### 15. Passive mode 에이전트가 영어로 응답
- 현재: "user가 한국어로 쓰면 한국어로 응답" 규칙 (harness.py)
- 문제: passive mode의 user_message는 시스템 생성 텍스트 + 영어 task context
  → 에이전트가 영어로 인식 → 영어 응답
- 해결: `project.language` 또는 `tenant.preferred_language` 필드 추가
