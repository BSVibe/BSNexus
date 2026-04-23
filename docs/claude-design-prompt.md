# BSNexus — Claude Design Brief

## 제품 한 줄
**사용자는 AI 회사를 고용한 파운더다.** 방향만 주면 내부 엔진이 알아서 분해·실행·감사하고, 사용자는 결과물과 결재 요청만 확인한다. "AI agent company" 철학은 유지하되, 이전의 조직도/태스크 보드/@멘션 채팅 UI는 전부 폐기했다.

## 타겟 톤
- 개발자용 SaaS (Better Stack / Vercel / Linear 레퍼런스)
- Dark-first, 절제된 비비드 악센트
- Plus Jakarta Sans (UI) + JetBrains Mono (코드)
- 4px grid

## 현재 IA (검토 포인트)
- **사이드바**: Dashboard / Settings — 2개뿐이라 비어 보임
- **프로젝트 상세**: Direction / Progress / Decisions 탭 + "Show Inside panel" 토글로 Inside 탭 활성화 → **토글이 어색함**. Inside를 넣으려면 그냥 탭으로, 빼려면 다른 곳으로.

**재설계 요청:** 위 두 지점을 포함해서 IA 전체를 다시 짜달라. 특히:
1. Inside 패널(감사/디버깅용, opt-in 성격)은 탭·사이드패널·페이지 중 어디가 맞나?
2. Direction(채팅)이 주 surface인데, Progress/Decisions 정보를 채팅에 inline으로 녹일 수 있나? 아니면 보조 패널로?
3. Dashboard는 프로젝트 리스트만 있는데, 전 프로젝트 aggregate(열린 결재 수, 최근 배송물 등)가 필요한가?

## 디자인 토큰 (spec v0.1.0)

### Gray scale
```
gray-950: #0a0b0f   (body bg)
gray-900: #111218   (card bg)
gray-850: #181926   (elevated)
gray-800: #1e2033   (hover)
gray-700: #2a2d42   (border)
gray-600: #3d4160   (inactive text)
gray-500: #5a5f7d   (placeholder)
gray-400: #8187a8   (secondary text)
gray-300: #a8adc6   (body text)
gray-200: #c8ccdb   (emphasized body)
gray-100: #e4e6ee   (headings)
gray-50:  #f2f3f7   (brightest)
```

### Brand / product accents
```
indigo-500  #6366f1   BSVibe (parent brand)
blue-500    #3b82f6   BSNexus  ← 이 제품의 primary
amber-500   #f59e0b   BSGateway
rose-500    #f43f5e   BSupervisor
emerald-500 #10b981   BSage
```

### Semantic aliases
```
bg-base:      gray-950
bg-surface:   gray-900
bg-elevated:  gray-850
bg-hover:     gray-800

text-primary:   gray-50
text-secondary: gray-400
text-tertiary:  gray-500
text-disabled:  gray-600

border-default: gray-700
border-subtle:  gray-800
border-strong:  gray-600

accent-default: blue-500   (BSNexus)
```

### Typography scale (rem / base 16px)
```
xs   12px / 16px  / 400   — caption, badge
sm   14px / 20px  / 400   — label, helper
base 16px / 24px  / 400   — body
lg   18px / 28px  / 500   — emphasized body
xl   20px / 28px  / 600   — subheading
2xl  24px / 32px  / 600   — section title
3xl  30px / 36px  / 700   — page title
```

### Spacing (4px base; 4 배수만 사용)
2 / 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96

### Radius
sm 4px / md 8px / lg 12px / xl 16px / full 9999px

### Shadows (다크 테마 — 그림자 아낌, 보더로 깊이)
sm  0 1px 2px rgba(0,0,0,0.3)
md  0 4px 12px rgba(0,0,0,0.4)
lg  0 8px 24px rgba(0,0,0,0.5)

### Motion
fast 100ms / normal 200ms / slow 300ms
easing-default  cubic-bezier(0.4, 0, 0.2, 1)

---

## 백엔드 데이터 모델 (실제 필드 — mock 데이터 만들 때 참고)

### Project
`id, tenant_id, name, description, status (design|active|paused|completed), bsage_workspace_id?, bsupervisor_policy_id?, created_at, updated_at`

### ConversationMessage
`id, project_id, role ("user"|"assistant"), content, request_id?, created_at`

### Request  (Direction에서 파생)
`id, project_id, origin_message_id, intent_summary, status (open|running|completed|abandoned), user_confirmed, created_at, updated_at`

### ExecutionRun  (내부, Inside에서만 노출)
`id, request_id, parent_run_id?, composition_snapshot_id?, status (pending|running|blocked|done), priority, output_type, actual_cost_cents, worker_id?, branch_name?, commit_hash?, error_message?, started_at?, completed_at?`

### CompositionSnapshot  (Inside에서 run 선택 시 펼침)
`id, request_id, source ("bsage"|"local"), persona_label, fit_score?, tools_allowed[], context_doc_refs[], system_prompt_ref, created_at`
- `context_doc_refs`: `[{path, title, score, excerpt_hash}]`
- `source="bsage"` = 지식 연동 참여, `"local"` = 템플릿만 사용 (degraded)

### Deliverable  (Progress 타임라인)
`id, project_id, request_id?, type (code|doc|design|data|url), title, status (draft|ready|delivered), current_version_id?, created_at`
+ DeliverableVersion: `version_int, storage_backend (git|object|url), content_hash, created_at`

### Decision  (Decisions 인박스)
`id, project_id, request_id?, question, options[], blocking (bool), resolved_at?, resolution?, resolved_by?, created_at`

### Integration (per-tenant)
3개 provider: `bsage`, `bsgateway`, `bsupervisor`.
각각: `enabled, base_url?, has_api_key, extra_config`.
BSupervisor만 `extra_config.timeout_ms (default 200)`, `extra_config.fail_mode ("open"|"closed")`.

---

## 필요한 화면 (스케치해 달라는 것들)

### 1. Global shell (사이드바 + 헤더)
현재 사이드바가 Dashboard/Settings 2개뿐. 후보:
- Projects 리스트 자체를 사이드바로 (각 프로젝트가 행)
- 또는 command palette(⌘K) + 최상단 project switcher
- 사용자 프로필 / 로그아웃 위치
- BSNexus 로고 + 모드 인디케이터 (어떤 sibling 서비스가 살아있는지 한눈에)

### 2. Dashboard / Home
전 프로젝트 단위 뷰.
- 프로젝트 카드 그리드 or 리스트 (이름, 상태, 최근 활동, 열린 Decision 수, 활성 Request 수)
- Quick-create CTA
- (선택) 전 프로젝트 aggregate: 이번 주 배송물, 블로킹 결재, 비용 (cost_records)
- 빈 상태 (프로젝트 0개)

### 3. Project — Direction (채팅, default)
**주 surface**. 핵심 UX:
- 메시지 스레드 (user/assistant 버블)
- 입력 박스 + Send (⌘+Enter)
- **요청 chip**: 메시지 전송 후 extractor가 `request`/`modification`으로 분류하면 하단에 떠서 요청이 열렸음/업데이트됐음을 알림
  - chit_chat/question은 chip 없음
  - chip에 "취소 / 병합 / 분할" 보조 액션
- 메시지 버블 하단에 연결된 request_id 표시 (예: "attached to request 84499258")
- 빈 상태: "Say something to kick off" — 파운더가 방향을 주는 곳임을 상기
- 오른쪽 사이드 패널 후보: 현재 열린 Request 요약 / 진행 중 Run count

### 4. Project — Progress (타임라인)
- 왼쪽: Deliverable 타임라인 (시간순), 각 항목에 type 배지(code/doc/design/data/url), status 배지(draft/ready/delivered), 현재 버전 정보
- 오른쪽: 3개 Trust Card (BSage 초록 / BSGateway 앰버 / BSupervisor 로즈)
  - 연결 상태 (on / off / degraded)
  - base_url 표시
  - 없을 땐 "Configure in Settings → Integrations" CTA
- 빈 상태: "No deliverables yet. Start a request in Direction."

### 5. Project — Decisions (결재 인박스)
- 블로킹 먼저, 해결된 건 아래로
- 각 decision 카드: question, 옵션 버튼 (options 배열), 블로킹 여부 배지, resolved 여부
- resolve: 옵션 클릭 or 자유 입력 + resolve 버튼
- 해결된 경우 resolution + resolved_by 노출
- 빈 상태: "Inbox clear."

### 6. Project — Inside (opt-in, 현재 토글 이슈)
**핵심 질문**: 이걸 탭으로 둘지, 사이드 패널로 둘지, 별도 페이지로 뺄지.
- 왼쪽: Request 트리 → 선택 시 해당 Request의 Run 트리 (parent-child)
- 오른쪽: 선택한 Run의 Composition Snapshot
  - persona_label, source badge (bsage=emerald / local=gray), fit_score
  - tools_allowed chips
  - system prompt (monospace, scrollable)
  - context_doc_refs 리스트
- 빈 상태: "Pick a run to inspect"

**개발자 트러스트용** 뷰. 사용자가 "왜 이렇게 처리됐나" 물을 때 여는 페이지. 평시엔 숨어있어도 됨.

### 7. Settings → Integrations
현재 탭이 잘 돼 있음. 리뷰 포인트:
- 3개 provider 카드 일관성 (BSage/BSGateway/BSupervisor)
- 각 카드: enable 토글, base URL, API key(저장되면 ••••), Test connection 버튼, 상태 배지 (healthy/unauthorized/unreachable/disabled)
- BSupervisor만 timeout(ms) + fail-mode (open/closed) 추가 필드
- Save 후 성공 피드백

### 8. Settings → 다른 섹션?
Executor 관리, Worker 토큰, 청구 등은 v0.x 이후. 지금은 Integrations만 있어도 됨.

### 9. 인증/로그인 / 에러 페이지
- `auth.bsvibe.dev` 리다이렉트 후 복귀 대기 화면
- 401 fallback
- 404 / 500

### 10. Empty / Loading / Error 상태들
각 surface 별로 빈 상태 + skeleton + 에러 카드 (dev bypass일 때 특히 API 실패 시 친절하게)

---

## 컴포넌트 프리미티브 (이미 코드에 있음 — 스타일 일관성을 위해 알려줌)
- Button (primary/secondary/ghost/icon, sm/md/lg)
- Modal (header/body/footer)
- Badge
- StatCard (metric with icon/label/value)
- Toast
- Header (page title + action slot)
- Sidebar (nav links + user footer)

새로 필요해 보이는 것:
- Chip (요청 chip용, 옵션 버튼이 있는 변형)
- Trust Card (accent color를 prop으로 받는)
- Timeline Item
- Tree node (Inside 왼쪽 패널)
- Code/prompt viewer (mono, scrollable, copy 버튼)
- Command palette (고려한다면)

---

## 제약 (반드시 지켜줘)

1. **라이트 모드 없음.** 다크 퍼스트가 제품 정체성.
2. **악센트는 아껴 써라.** 버튼/링크/배지/중요 상태만. 배경에 악센트 색 넓게 깔지 말 것.
3. **4px 그리드.** 13px, 7px 같은 임의 값 금지.
4. **Plus Jakarta Sans 일관**, 코드·해시·ID만 JetBrains Mono.
5. **제품 색상 = CSS 변수.** 같은 컴포넌트가 서브도메인별로 색만 바뀌도록 설계.
6. **깊이는 border + bg 명도 차이**로 표현. 큰 그림자 남용 금지.
7. **ID/UUID 표기**: 항상 앞 8자 truncate + title에 full id tooltip.
8. **relative time** 우선 ("3h ago"), 툴팁에 ISO datetime.

---

## 산출물 요청

가능하면:
1. IA 제안서 1장 (사이드바/탭/페이지 트리)
2. 화면 1~9 각각의 wireframe (high-fidelity dark mockup)
3. 위에서 제기한 토글 이슈에 대한 결정 + 근거
4. 추가로 필요해 보이는 화면이 있다면 제안 (e.g., cost dashboard?, audit timeline?)
5. 가능하면 Inspector/Command palette 같은 개발자 친화 요소 고민
