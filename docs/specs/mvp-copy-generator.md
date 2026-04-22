# MVP Spec: AI Copy Generator

**Product**: BSNexus AI Copy Generator
**Author**: Product Manager
**Date**: 2026-04-11
**Status**: Draft

---

## 1. Problem Statement

소상공인과 스타트업은 마케팅 카피(광고 문구, SNS 포스트, 랜딩페이지 텍스트 등)를
전문 카피라이터 없이 직접 작성해야 하는 경우가 많다. 업종별 특성을 반영한
고품질 카피를 빠르게 생성하는 도구가 필요하다.

## 2. Target User

| Segment | Description |
|---------|-------------|
| **Primary** | 소상공인, 1인 마케터, 스타트업 마케팅 담당자 |
| **Secondary** | 프리랜서 카피라이터, 마케팅 에이전시 |

## 3. Core Features (MVP Scope)

### 3.1 업종별 카피 생성

사용자가 업종을 선택하면 해당 업종에 최적화된 카피를 생성한다.

**지원 업종 (Phase 1)**:

| Category | Sub-categories |
|----------|---------------|
| **F&B** | 카페, 레스토랑, 베이커리, 배달음식 |
| **뷰티/헬스** | 네일샵, 헤어살롱, 피트니스, 피부관리 |
| **교육** | 학원, 온라인 강의, 과외 |
| **리테일** | 의류, 잡화, 온라인쇼핑몰 |
| **전문서비스** | 법률, 세무, 부동산, 인테리어 |

**카피 유형**:

| Type | Output | Max Length |
|------|--------|------------|
| SNS 포스트 | Instagram/Facebook 캡션 | 300자 |
| 광고 카피 | 네이버/Google 광고 헤드라인 + 설명 | 헤드라인 30자 + 설명 90자 |
| 블로그 인트로 | 블로그 포스트 도입부 | 500자 |
| 프로모션 문구 | 할인/이벤트 안내 | 200자 |
| 메뉴/상품 설명 | 개별 상품 소개 | 150자 |

**입력 필드**:

```
- 업종 선택 (required): dropdown
- 카피 유형 (required): dropdown
- 핵심 키워드 (required): 1~5개, 쉼표 구분
- 브랜드명 (optional): text
- 타겟 고객 (optional): text (예: "20~30대 직장인 여성")
- 프로모션 정보 (optional): text (예: "오픈 기념 20% 할인")
- 톤/스타일 (required): 아래 3.2 참조
```

**출력**: 카피 3개 동시 생성 (사용자가 선택/편집 가능)

### 3.2 톤/스타일 선택

사용자가 원하는 톤을 선택하면 카피의 어조가 조정된다.

| Tone ID | Label | Description | Example |
|---------|-------|-------------|---------|
| `professional` | 전문적 | 신뢰감, 격식체 | "검증된 전문가가 제공하는 맞춤 솔루션" |
| `friendly` | 친근한 | 구어체, 이모지 활용 | "오늘도 힘든 하루? 여기서 힐링하세요 :)" |
| `witty` | 재치있는 | 유머, 말장난 | "커피 한 잔의 여유, 가격은 반 잔" |
| `luxurious` | 고급스러운 | 프리미엄, 절제된 표현 | "당신만을 위한 프라이빗 케어" |
| `urgent` | 긴급한 | 한정, 희소성 강조 | "단 3일! 이 기회를 놓치지 마세요" |
| `emotional` | 감성적 | 스토리텔링, 공감 | "매일 아침, 갓 구운 빵 향기로 시작되는 하루" |

**커스텀 톤**: MVP에서는 미지원. Phase 2에서 사용자 정의 톤 프리셋 추가 예정.

### 3.3 구독 모델 ($9.9/월)

#### Tier 구조

| | **Free** | **Pro ($9.9/월)** |
|---|----------|-------------------|
| 월간 생성 횟수 | **10회** | **500회** |
| 카피 유형 | SNS 포스트, 광고 카피 | 전체 5종 |
| 톤/스타일 | 전문적, 친근한 (2종) | 전체 6종 |
| 업종 | 전체 | 전체 |
| 생성 결과 수 | 1개/요청 | 3개/요청 |
| 히스토리 보관 | 최근 10건 | 무제한 |
| 카피 즐겨찾기 | 5건 | 무제한 |
| 내보내기 (CSV) | X | O |

> **1회 = 1 API 요청** (Pro는 3개 동시 생성해도 1회로 카운트)

#### 결제

- **결제 수단**: Stripe Checkout (카드)
- **과금 주기**: 월간 자동결제 (매월 가입일 기준)
- **무료 체험**: 최초 가입 시 Pro 7일 무료 체험
- **해지**: 즉시 해지 가능, 남은 기간 동안 Pro 유지
- **초과 시**: 생성 버튼 비활성화 + "이번 달 사용량을 모두 소진했습니다" 안내

#### 사용량 추적

```
UsageRecord:
  - tenant_id: UUID
  - user_id: UUID
  - period_start: date (매월 1일 or 가입일)
  - period_end: date
  - used_count: int
  - limit_count: int (Free=10, Pro=500)
```

- 사용량은 Redis counter로 실시간 추적, 일 1회 DB 동기화
- 월 초(또는 결제 갱신일)에 자동 리셋

## 4. User Flow

```
[랜딩페이지] → [회원가입/로그인]
    ↓
[대시보드]
    ├── 잔여 사용량 표시 (예: "이번 달 3/10회 사용")
    ├── [새 카피 만들기] → [카피 생성 폼]
    │       ├── 업종 선택
    │       ├── 카피 유형 선택
    │       ├── 톤/스타일 선택
    │       ├── 키워드 입력
    │       └── [생성하기] → [결과 화면]
    │               ├── 카피 1~3개 표시
    │               ├── [복사] [즐겨찾기] [재생성]
    │               └── [편집] (인라인 수정)
    ├── [히스토리] → 과거 생성 목록
    └── [구독 관리] → Stripe Customer Portal
```

## 5. Technical Architecture

### 5.1 Backend (BSNexus 기반)

기존 BSNexus 인프라를 활용한다.

**신규 모듈**:

| Module | Purpose |
|--------|---------|
| `api/copy_gen.py` | 카피 생성 API 라우터 |
| `core/copy_engine.py` | 업종별 프롬프트 조합 + LiteLLM 호출 |
| `core/subscription.py` | Stripe 연동 + 사용량 관리 |
| `models/copy.py` | CopyRequest, CopyResult, UsageRecord 모델 |
| `models/subscription.py` | Subscription, PaymentHistory 모델 |
| `schemas/copy.py` | Request/Response Pydantic 스키마 |
| `schemas/subscription.py` | 구독 관련 스키마 |

**API Endpoints**:

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/api/v1/copy/generate` | 카피 생성 | Required |
| `GET` | `/api/v1/copy/history` | 생성 히스토리 | Required |
| `POST` | `/api/v1/copy/{id}/favorite` | 즐겨찾기 토글 | Required |
| `GET` | `/api/v1/copy/usage` | 사용량 조회 | Required |
| `GET` | `/api/v1/copy/industries` | 업종 목록 | Public |
| `GET` | `/api/v1/copy/tones` | 톤/스타일 목록 | Public |
| `POST` | `/api/v1/subscription/checkout` | Stripe 결제 세션 생성 | Required |
| `POST` | `/api/v1/subscription/webhook` | Stripe 웹훅 수신 | Stripe Sig |
| `GET` | `/api/v1/subscription/status` | 구독 상태 조회 | Required |
| `POST` | `/api/v1/subscription/portal` | Customer Portal URL | Required |

### 5.2 Copy Engine (Prompt Strategy)

```python
# 핵심 구조 (pseudo)
system_prompt = INDUSTRY_CONTEXT[industry] + TONE_INSTRUCTION[tone]
user_prompt = f"""
업종: {industry}
카피 유형: {copy_type}
키워드: {keywords}
브랜드: {brand_name}
타겟: {target_audience}
프로모션: {promotion_info}

위 정보를 바탕으로 {result_count}개의 카피를 생성하세요.
"""
```

- **업종 컨텍스트**: 업종별로 사전 정의된 도메인 지식 + 성공 카피 예시
- **톤 인스트럭션**: 톤별 어조 가이드라인 + few-shot 예시
- **모델**: `settings.default_llm_model` (LiteLLM 경유)
- **Structured Output**: Pydantic 모델로 JSON 파싱하여 안정적 출력 보장

### 5.3 Frontend

| Page | Route | Description |
|------|-------|-------------|
| CopyDashboard | `/copy` | 사용량 + 최근 생성 목록 |
| CopyGenerator | `/copy/new` | 생성 폼 |
| CopyResult | `/copy/result/:id` | 결과 보기/편집 |
| CopyHistory | `/copy/history` | 전체 히스토리 |
| Subscription | `/subscription` | 구독 관리 |

### 5.4 Data Models

```
CopyRequest (DB)
  - id: UUID (PK)
  - tenant_id: UUID (FK)
  - user_id: UUID
  - industry: str
  - copy_type: str
  - tone: str
  - keywords: list[str]
  - brand_name: str | None
  - target_audience: str | None
  - promotion_info: str | None
  - created_at: datetime

CopyResult (DB)
  - id: UUID (PK)
  - request_id: UUID (FK → CopyRequest)
  - content: str
  - is_favorite: bool = False
  - is_edited: bool = False
  - edited_content: str | None
  - created_at: datetime

Subscription (DB)
  - id: UUID (PK)
  - tenant_id: UUID (FK)
  - stripe_customer_id: str
  - stripe_subscription_id: str | None
  - plan: enum("free", "pro")
  - status: enum("active", "canceled", "past_due", "trialing")
  - current_period_start: datetime
  - current_period_end: datetime
  - trial_end: datetime | None
  - created_at: datetime
  - updated_at: datetime
```

## 6. Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| **응답 속도** | 카피 생성 3초 이내 (P95) |
| **가용성** | 99.5% uptime |
| **보안** | Stripe 웹훅 서명 검증, API key 노출 금지 |
| **데이터** | 사용자 카피 데이터 90일 보관 (Free), 무제한 (Pro) |
| **확장성** | 업종/톤 추가 시 코드 변경 최소화 (config-driven) |
| **다국어** | MVP는 한국어만. Phase 2에서 영어/일본어 |

## 7. Success Metrics

| Metric | Target (Launch +3개월) |
|--------|----------------------|
| 가입 전환율 (랜딩 → 가입) | 15% |
| Free → Pro 전환율 | 5% |
| 월간 활성 사용자 (MAU) | 1,000명 |
| 평균 생성 횟수/유저/월 | 20회 (Pro) |
| 카피 복사율 (생성 → 복사) | 60% |
| 월간 반복 매출 (MRR) | $500 |
| 이탈율 (월간) | < 10% |

## 8. Out of Scope (Phase 2+)

- [ ] 커스텀 톤/스타일 프리셋 저장
- [ ] 팀 워크스페이스 (멀티유저)
- [ ] A/B 테스트용 카피 변형 생성
- [ ] 이미지 + 카피 조합 생성
- [ ] Enterprise 요금제 ($49.9/월, 무제한)
- [ ] 영어/일본어 카피 생성
- [ ] 카피 성과 추적 (클릭률, 전환율 연동)
- [ ] 브랜드 가이드라인 업로드
- [ ] Chrome Extension (SNS 직접 붙여넣기)
- [ ] API 제공 (외부 연동)

## 9. Timeline

| Phase | Duration | Deliverables |
|-------|----------|-------------|
| **Design** | 1주 | UI 와이어프레임, DB 스키마 확정 |
| **Backend Core** | 2주 | Copy Engine + API + 사용량 관리 |
| **Stripe 연동** | 1주 | 결제 + 웹훅 + Customer Portal |
| **Frontend** | 2주 | 전체 UI 구현 |
| **QA & Polish** | 1주 | E2E 테스트, 버그 수정, 성능 최적화 |
| **Launch** | - | 총 7주 |

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM 비용 초과 | 마진 감소 | Pro 500회 제한 + 모델 비용 모니터링 |
| 카피 품질 불만 | 이탈 증가 | 업종별 프롬프트 튜닝 + 사용자 피드백 루프 |
| Stripe 결제 오류 | 매출 손실 | 웹훅 재시도 + 수동 복구 프로세스 |
| 한국 시장 결제 | 전환율 저하 | 원화 표시 (약 ₩14,900) + 토스페이먼츠 Phase 2 |

---

*Last updated: 2026-04-11*
