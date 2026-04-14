# Issues to Fix (Post Active/Passive Split)

시나리오 테스트 결과 기반 이슈 목록 (2026-04-15 업데이트).

## 시나리오 결과 (2026-04-15, 세션 3)

| 항목 | 상태 | 비고 |
|------|------|------|
| 프로젝트 생성 | PASS | API + Playwright 모두 |
| Phase/Task 생성 | PASS | 1-5 phases, 3-54 tasks (실행마다 다름) |
| 한국어 응답 | PASS | 전 에이전트 |
| Delegation chain | PASS | CMO → 3-11 agents |
| Passive dispatch | PASS | GlobalDispatcher → claim_task → execute |
| claim_task idempotent | PASS | dispatcher pre-transition 후 agent claim 성공 |
| complete_task | PASS | Product_Manager, CPO task 완료 |
| file_write | PASS | CPO 4회, Product_Manager 1회 (context/strategy 파일) |
| **코드 파일 생성** | **FAIL** | .py/.ts 등 코드 파일 없음 |
| **디자인 파일** | **FAIL** | .bsd 파일 없음 (이전 세션에선 5개 생성됨) |
| **빌드 가능 프로젝트** | **FAIL** | 기획 문서만 산출, 코드 미산출 |

## Critical — 빌드 가능 프로젝트 산출에 필요

### 1. ★ 에이전트별 FIFO 큐 필요
- **증상**: Backend_Engineer, Frontend_Engineer가 active delegation으로만 호출 → create_task만 하고 끝
  또는 delegation이 아예 안 되어 체인에서 빠짐
- **원인**: active/passive 이중 dispatch 문제. active mode에서는 file_write 도구가 없음.
  defer 로직이 타이밍에 따라 너무 공격적이거나 너무 느슨함.
- **해결**: 에이전트별 asyncio.Queue FIFO 큐
  - active 요청(chat @mention)과 passive 요청(task dispatch) 모두 같은 큐에 enqueue
  - 에이전트당 1개 worker가 순차 처리
  - active 끝나면 passive 차례, passive 끝나면 active 차례
  - 현재 `defer` 로직 대체 — 타이밍 문제 해결
- **설계**: `core/agent_queue.py` 신규 모듈
  ```python
  class AgentRequest:
      mode: Literal["active", "passive"]
      message: str
      task_id: uuid.UUID | None
      task_context: str | None
  
  class AgentQueueManager:
      _queues: dict[uuid.UUID, asyncio.Queue[AgentRequest]]
      _workers: dict[uuid.UUID, asyncio.Task]
      
      async def enqueue(agent_id, request)
      async def _process_queue(agent_id)  # single worker per agent
  ```
- **관련 파일**: `agent_chat.py` (active dispatch), `global_dispatcher.py` (passive dispatch)

### 2. CTO → Engineer 멘션 체인 미도달
- **증상**: CMO가 3명만 mention (Content_Writer, Product_Manager, CPO), CTO 안 mention
  → CTO가 Backend/Frontend_Engineer를 mention하는 2차 체인이 시작 안 됨
- **원인**: CMO가 만든 task에 Content_Writer/Product_Manager/CPO를 assign → defer로 active chain 차단 → CTO까지 chain이 안 감
- **해결**: #1 (에이전트 큐) 구현하면 자연스럽게 해결
  - CMO가 @CTO → active queue에 enqueue → CMO 끝나면 CTO 처리
  - CTO가 @Backend_Engineer → active queue에 enqueue
  - GlobalDispatcher가 task dispatch → passive queue에 enqueue
  - 같은 에이전트의 active/passive가 순차 처리됨

### 3. Phase 자동 진행 미확인
- **증상**: Market Research phase만 생성, 구현 phase 미생성
- **코드**: `GlobalDispatcher._advance_phase_if_complete()` 존재
- **원인**: #2와 동일 — CTO까지 체인이 안 가서 구현 phase 자체가 안 만들어짐
- **해결**: #1 해결 시 자연 해결 예상

## Medium — 동작하지만 개선 필요

### 4. Active/Passive 이중 dispatch (부분 해결)
- **현재 상태**: `delegation_deferred_to_passive` 로직 추가됨
  - DB + actions 기반 체크로 assigned agent는 active delegation 스킵
  - 하지만 타이밍에 따라 과하거나 부족함
- **최종 해결**: #1 (에이전트 큐)로 대체하면 defer 로직 불필요

### 5. Task 중복 생성
- **증상**: 같은 제목의 task가 여러 개
- **원인**: 여러 에이전트가 active mode로 동시 호출 → 각자 같은 task 생성
- **해결**: `list_tasks` 결과에서 중복 체크 강화, 또는 title uniqueness per phase

### 6. `_delegation_text` 임시 속성
- **상태**: `msg._delegation_text = ...` 로 ConversationMessage에 동적 속성 추가
- **영향**: type safety 없음
- **해결**: 정식 필드로 전환

### 7. vLLM 병목 + hang
- **증상**: sequential inference로 11개 agent 동시 처리 불가
- **continuous batching 시도**: `--continuous-batching` 플래그 추가 → 모델 로드 성공하지만 hang 발생
- **현재 상태**: batching 없이 운영, sequential 처리
- **대안**: ollama `OLLAMA_NUM_PARALLEL=4` 또는 더 작은 모델
- **장기**: GPU 서버 또는 API 기반 LLM

## Low — 리팩토링 시 처리

### 8. Structlog + SQL echo 혼재
- echo=False 또는 별도 로그 파일

### 9. CRITICAL_RULES_INLINE backward compat alias
- `CRITICAL_RULES_INLINE = ACTIVE_MODE_RULES` — 테스트 정리 후 제거

### 10. test_active_passive_mode.py 미작성
- task_assignment.py 단위 테스트 필요
- GlobalDispatcher passive dispatch 테스트 필요

### 11. 중지해도 큐잉된 에이전트가 계속 실행됨
- 프로젝트 레벨 stop flag 필요

### 12. vLLM 인프라 불안정
- Colima crash — vLLM + Docker 동시 메모리 사용
- kill -9 재시작 필요
