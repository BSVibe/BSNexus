# 세션 5 위임 프롬프트

## 컨텍스트

BSNexus Company OS — AI 에이전트들이 org chart 기반으로 협업하는 시스템.
`feat/plan-view-overhaul` 브랜치에서 작업 중.

**현재 달성된 것**: 프론트엔드에서 프로젝트 생성 후 "@CMO 할 일 관리 웹앱 만들어줘" 입력 → CMO가 phase/task 생성 → 에이전트들에게 위임 → passive mode에서 claim_task → file_write(코드) → complete_task 플로우 동작. Express.js REST API (server.js) 산출 확인.

**인수인계서**: `/Users/blasin/.claude/projects/-Users-blasin/memory/project_bsnexus_task_centric_refactor.md`
**이슈 목록**: `/Users/blasin/Works/BSNexus/feat-company-os/docs/issues-to-fix.md`

## 이번 세션 목표

### 1순위: Task 중복 방지 (issues #5)

active mode 에이전트들이 같은 task를 반복 생성하는 문제. 23개 task 중 대부분 중복.

**방향**: `create_task` 도구에서 같은 phase 내 제목 유사도 체크. 정확 일치 시 기존 task 반환, 높은 유사도 시 경고 메시지.

**파일**: `backend/src/tools/plan_tools.py` — `CreateTaskTool.execute()`
**테스트**: `backend/tests/test_tools/test_plan_tools.py`

### 2순위: CMO self-assign 방지 (issues #6)

CMO가 create_task에서 assignee를 자기 자신으로 설정. active mode 에이전트는 기획/위임만 해야 하는데 자기한테 실행 task를 할당.

**방향**: create_task에서 현재 에이전트(ctx.agent_name)를 assignee로 설정하면 경고 반환 + assignee를 None으로 리셋. 또는 프롬프트에 규칙 추가.

**파일**: `backend/src/tools/plan_tools.py`, `backend/src/core/harness.py` (ACTIVE_MODE_RULES)

### 3순위: .bsd 디자인 안정화 (issues #8)

Designer가 create_screen을 간헐적으로만 호출. passive 프롬프트가 "create_screen for designs"라고만 되어 있어서 file_write(문서)로 대체하는 경향.

**방향**: design capability 있는 에이전트의 passive 프롬프트에 구체적 가이드 추가. 또는 `assemble_system_prompt()`에서 agent.capabilities에 "design"이 있으면 추가 지시 주입.

**파일**: `backend/src/core/harness.py` — PASSIVE_MODE_RULES 또는 assemble_system_prompt()

### 선택: vLLM 안정성 (issues #7)

qwen3-coder-30b가 동시 요청 ~10분에 hang. 코드 문제가 아니라 인프라 이슈.

**방향 A**: ollama로 전환 (`OLLAMA_NUM_PARALLEL=2`)
**방향 B**: vLLM health check + 자동 재시작

## 실행 환경

```bash
# 인수인계서의 "서버 실행" 섹션 참고
colima start --cpu 8 --memory 16
docker compose -f .devcontainer/docker-compose.yml up -d

# Backend 시작
docker exec bsnexus-feat-company-os-app-1 bash -c "kill \$(pgrep -f uvicorn) 2>/dev/null"
sleep 2
docker exec bsnexus-feat-company-os-app-1 bash -c "cd /workspace && \
  PYTHONPATH=/workspace REDIS_URL=redis://redis:6379 \
  DATABASE_URL='postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus' \
  E2E_TEST_TOKEN=e2e-scenario-test-token \
  E2E_TEST_USER_TENANT_ID=ab8bfb15-cb63-4068-a600-54b02b33396d \
  nohup uv run --project backend uvicorn backend.src.main:app \
  --host 0.0.0.0 --port 8000 --reload > /tmp/backend.log 2>&1 &"

# vLLM (qwen3-coder-30b)
kill -9 $(lsof -ti:8888) 2>/dev/null
~/.venvs/vllm-mlx/bin/vllm-mlx serve mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit \
  --host 0.0.0.0 --port 8888 --enable-auto-tool-choice --tool-call-parser qwen \
  --reasoning-parser qwen3 --served-model-name qwen3-coder --enable-prefix-cache

# DB 정리
docker exec bsnexus-feat-company-os-postgres-1 psql -U bsnexus -d bsnexus -c \
  "DELETE FROM conversation_messages; DELETE FROM task_activities; DELETE FROM task_history; DELETE FROM tasks; DELETE FROM phases; DELETE FROM projects;"

# 단위 테스트
docker exec bsnexus-feat-company-os-app-1 bash -c \
  "cd /workspace && uv run --project backend pytest backend/tests/ -v --timeout=60"

# 시나리오 테스트
bash scripts/scenario-test.sh
```

## 하지 않을 것

- 코딩을 파이프라인에 강제하지 않음 (코딩 무관 프로젝트에 영향)
- delegation filter 추가 안 함 (큐 방식이라 active/passive 순차 처리됨)
- CODING_SKILL 프롬프트 추가 안 함 (에이전트 job_description + passive 가이드로 충분)
