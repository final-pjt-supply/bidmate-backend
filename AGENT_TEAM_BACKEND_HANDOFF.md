# 챗봇 — 백엔드 현황 & 에이전트팀 결정 요청

> **한 줄:** 백엔드는 **빈도·횟수·깊이(레이트리밋·상한)** 를 API 경계에서 이미 막았음.
> **문맥을 어떻게 자르고 토큰을 어떻게 아낄지**는 에이전트 내부 결정이라 아래를 정해주세요.

## TL;DR
- **정해줘:** ① 문맥 방식(20턴캡 vs 슬라이딩) ⭐ · ② SessionContext 상한 · ③ response_meta 구조 · ④ 식별 단위(회사/유저)
- **바꾸기 전 알려줘:** 응답 스키마 · session_context 하위호환 · 새 외부 의존
- **알고만 있어줘:** SHA 범프로 반영 · 동기·무스트리밍 · company_id는 백엔드가 줌

---

## ✅ 백엔드가 이미 막은 것 (구현·배포 완료)

| # | 방어 | 기준 | 초과 시 | 저장 위치 | env |
|---|---|---|---|---|---|
| 1 | 분당 레이트리밋 | 회사당 **10회/분** | `429` + `Retry-After` | 인메모리(60초 슬라이딩 창) | `RATE_LIMIT_PER_MIN` |
| 2 | 동시성 가드 | 회사당 **동시 3개** | `429` + `Retry-After` | 인메모리(슬롯) | `CHAT_CONCURRENCY_MAX` |
| 3 | 일일 상한 | 회사당 **500회/일** | `429`(자정까지 `Retry-After`) | RDS `chat_daily_usage`(원자 UPSERT) | `CHAT_DAILY_MAX` |
| 4 | 세션 문맥 캡 | 세션당 **20턴** | `200` + `action=clarify` "새 대화 시작" | ChatSession user 메시지 수 | `SESSION_MAX_TURNS` |
| 5 | 세션 동시턴 | 세션당 **1턴** | `409`(응답 중 재입력) | 인메모리 `_inflight` set | — |
| 6 | 공백 질문 | trim 후 빈 값 | `422`(LLM 미도달) | — | — |
| 7 | 쿼리 길이 | **500자** 초과 | `422` | — | — |
| 8 | 인증 | 토큰 없음 | `401` | Cognito JWT | — |

**핵심 규칙**
- **기준은 IP가 아니라 회사(company_id)** — 토큰에서 도출. 와이파이/모바일로 IP 바꿔도 같은 회사면 한도 유지(IP 로테이션 면역). 순수 IP 차단(익명 폭주)은 Nginx 계층 = 프론트/인프라 몫.
- **카운트 = 유저 질문 수 1회 = 1.** 내부 Bedrock 다중 호출은 안 셈(유저 관점 "분당 질문 수"). **검증실패(422)는 제외**(LLM 미도달), **에이전트 실패(502)는 포함**(LLM이 돌아 비용 발생 → 유저 쿼터 소진).
- 인메모리(1·2·5)는 **단일 컨테이너라 정확**. 다중 인스턴스/Lambda 전환 시 **Redis 이관**(그전엔 한도가 N배 느슨해질 뿐 동작). 일일(3)은 재배포에도 안 리셋되게 RDS.
- **소프트캡(4) 응답 형태:** `200` + `{action:"clarify", clarify_message:"대화가 너무 길어졌어요. 새 대화를 시작해 주세요."}`. 에러 아님 — 세션이 새 턴만 안 받음(기존 메시지 유지).

---

## 🙋 에이전트팀이 정해줘야 할 것

### 🔴 1순위 — 정해야 백엔드가 움직임

**① 문맥 관리 방식** ⭐ *(협의 최우선)*
- **지금(백엔드):** 20턴 **소프트캡**. 21턴째에 LLM 안 부르고 "새 대화 시작" 안내(`chat_service.py`, `SESSION_MAX_TURNS=20`). 에이전트가 `last_summary`를 왕복시켜서 토큰이 폭증하진 않으므로 **가드레일 성격**.
- **정할 것:** **슬라이딩 윈도우**(최근 N턴만 문맥 유지)로 전환할지 + **N값**.
- **정해지면 백엔드가:**
  - 소프트캡 로직 변경/제거
  - **"이전 대화 일부 요약됨" 플래그를 응답에 추가**(프론트 안내용) → §API 계약 참고
- **왜 에이전트팀:** 문맥을 얼마 잘라도 답 품질이 유지되는지는 에이전트만 판단 가능.

### 🟡 계약 확정 — 백엔드는 저장·전달만

**② `SessionContext`(JSONB) 스키마 · 크기 상한**
- **지금(백엔드):** `chat_sessions.session_context`에 **불투명 JSONB로 저장만**. 다음 턴에 그대로 되먹임. 내용(`last_bid_ids`, `last_summary`, `last_filters` 등)은 **에이전트가 정의**.
- **정할 것:** 스키마 + **무한 증가 방지 상한**(특히 `last_summary`가 계속 커지는 것).
- **정해지면 백엔드가:** 저장 시 **크기 가드**(상한 초과 시 거부/경고) — RDS JSONB 비대 방지.

**③ `response_meta`(citations · action) 구조**
- **지금(백엔드):** `chat_messages.response_meta`에 `{action, citations, redirect_filters}` 저장·전달. `citations = [{bid_id, file_id, chunk_idx, text}]`.
- **정할 것:** 이 **구조를 확정**(인용·액션 필드).
- **정해지면:** **프론트 렌더 계약 고정** — 구조를 바꾸면 프론트가 깨짐.

### 🟢 큰 변경 — 결정에 따라 백엔드 구조 바뀜

**④ 식별 단위: 회사(company_id) → 유저?**
- **지금(백엔드):** 레이트리밋·세션이 **company_id 기준**(JWT에서). 유저 sub는 토큰에 있지만 키로 안 씀.
- **정할 것:** 회사 계정 공유 남용 대응으로 **유저 단위** 세분화가 필요한지.
- **정해지면 백엔드가:** 토큰의 **user sub를 레이트리밋·세션 키로 추가**(전면 변경, 큰 작업).

---

## 📄 API 계약 (참고 — 바꾸려면 사전 협의)

**요청** `POST /agent/chat` — `AgentChatRequest`
| 필드 | 타입 | 비고 |
|---|---|---|
| `query` | str (1~500자, trim 필수) | 공백만이면 422 |
| `entry_bid_id` | str? | "이 공고에 대해 질문하기" 진입 시 해당 bid 스코프 |
| `session_id` | str? | 없으면 새 세션, 있으면 이어쓰기 |
| ~~company_id~~ | — | **요청 본문에 없음** — 토큰에서만(멀티테넌시) |

**응답** `AgentChatResponse` (클라 노출)
- `session_id` · `action`(answer/clarify/redirect) · `answer` · `clarify_message` · `redirect_filters` · `citations`
- ⚠ `session_context`는 **클라에 안 내려감** — 서버가 세션에 저장하고 다음 턴에 되먹임.

**에이전트 반환** `AgentResponse` (백엔드가 받는 것 = 계약 핵심)
```
{ action, answer, clarify_message, redirect_filters(Filters), citations[], session_context(SessionContext) }
```
→ **이 구조를 바꾸면 백엔드 저장·프론트 렌더가 다 깨짐.**

**에러 매핑**
| 코드 | 언제 |
|---|---|
| `401` | 무토큰 |
| `409` | 응답 생성 중 같은 세션 재입력 |
| `404` | 남의 세션(IDOR) — 존재 은닉 |
| `422` | 공백/500자 초과 |
| `429` | 레이트리밋(분당·동시·일일) |
| `502` | 에이전트 실패(Bedrock 스로틀 등) |

---

## 🗄️ 챗 세션 저장 구조 (RDS)

| 테이블 | 역할 | 핵심 컬럼 |
|---|---|---|
| `chat_sessions` | 대화방 | `session_id`(UUID) · `company_id` · **`session_context`(JSONB)** · `title` · `deleted_at` |
| `chat_messages` | 메시지(발화) | `role`(user/assistant) · `content` · **`response_meta`(JSONB)** |
| `chat_daily_usage` | 일일 카운터 | `(company_id, usage_date)` · `call_count` |

**턴 처리(실패 경계):** `open_turn`(user 메시지 먼저 커밋) → `run_agent` → `close_turn`(성공 시 assistant + session_context 커밋).
→ 에이전트가 실패해도 **유저 질문은 남아 재시도 가능**.

---

## 📌 바꾸기 전 백엔드와 맞출 계약

| 항목 | 주의 |
|---|---|
| **응답 스키마**(`AgentResponse`) | 구조 바꾸면 백엔드 저장·프론트 렌더 깨짐 |
| **`session_context` 하위호환** | 저장된 걸 다음 턴에 역직렬화 → 형태 바꾸면 **옛 세션이 깨짐**. 하위호환 유지 or 마이그레이션 협의(예: `axis_class`에 `info`를 과거호환으로 남긴 것처럼) |
| **새 외부 의존 추가**(새 API·모델) | 접속정보를 **백엔드 `.env`+CD 시크릿**에 넣어야 함 → **반드시 백엔드에 통보** |

**현재 백엔드가 주입 중인 에이전트 config**
- **Bedrock**: `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` (⚠ 비표준 이름) — Haiku 라우터 + Sonnet
- **OpenSearch**: endpoint / user / password / index (RAG 벡터검색)
- **Cloudflare**: `CF_ACCOUNT_ID` / `CF_API_TOKEN` / embedding model (질의 임베딩 BGE-M3)

---

## ℹ️ 알고만 있으면 되는 운영 사항

- **반영은 자동 아님:** 에이전트 main 머지 → 백엔드가 **SHA 범프 PR**로 반영(`requirements.txt`에 커밋 SHA 고정). **풀 리빌드 ~10~15분**(ARM64). 백엔드 CI가 **너희 코드를 설치·pytest·import 스모크** → `run_agent`/스키마 깨면 **백엔드 CI가 먼저 빨개짐**.
- **동기 인프로세스 실행:** 에이전트가 백엔드 프로세스 안에서 **동기 실행**(HTTP 아님, `from agents.run import run_agent`) → **느린 호출이 백엔드 스레드 점유**. 백엔드가 **25~30초 타임아웃** 도입 예정. **스트리밍 없음**(원하면 백+에이전트+프론트 큰 변경).
- **company_id는 백엔드가 넘김:** 에이전트는 **백엔드가 토큰에서 도출한 값만** 사용 — 유저 입력 신뢰 ❌. 검색·자격 필터에 이 값을 스코프로.
