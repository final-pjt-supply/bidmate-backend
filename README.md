# BidFriend Backend

나라장터(조달청) 공고를 **조회·서빙**하고, **대화 에이전트**와 **회사 자격 매칭**을
제공하는 API 서버. 수집·추출 파이프라인이 채워둔 데이터를 프론트/에이전트에 내려준다.

> 데이터 파이프라인(수집→추출→임베딩→적재)은 별도 리포에 있다. 이 리포는
> **그 결과(bid_table 등)를 읽어 서빙하는 API 계층**이다.

## 기술 스택

FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL(주) · Cognito JWT(인증)
· AWS Bedrock(대화 에이전트) · OpenSearch(에이전트 검색) · S3(이벤트 적재)
· Uvicorn · Nginx · Docker · ECR · SSM · pytest

## 구조 (모듈러 모놀리스, 의존성 단방향)

```
app/
  api/        FastAPI 라우터·의존성·응답 스키마(DTO)
  services/   비즈니스 로직(정책·조립·페이징)
  agents/     대화 에이전트 연결 (별도 서비스 HTTP 호출 + 세션 왕복)
  domain/     도메인 모델·enum (추출 파이프라인과 공유)
  infra/      DB 세션·ORM·repository / auth(Cognito 검증) / s3(이벤트 싱크)
  config.py   설정(.env 로딩)
  main.py     앱 진입점 + Lambda 핸들러
tests/api/    계약 테스트 + repository 통합 테스트
alembic/      마이그레이션 (API 소유 테이블만 관리, coexist)
deploy/       EC2 Blue/Green 배포·Nginx·AWS 정책
```

## 실행 (서버)

### 로컬 개발 서버

PowerShell 기준:

```powershell
# 최초 1회: 가상환경과 의존성 준비
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app\requirements.txt

# .env에 로컬 또는 SSH 터널 접속 정보를 입력한 뒤 실행
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- 상태 확인: [http://localhost:8000/health](http://localhost:8000/health)
- Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)
- 프론트는 기본적으로 `http://localhost:8000`을 바라보게 설정한다.
- 인증 없이 고정 회사로 확인하려면 로컬 `.env`에 `AUTH_DISABLED=true`와
  `DEV_COMPANY_ID=<회사 ID>`를 넣는다. 운영 환경에서는 절대 활성화하지 않는다.
- 매칭 주기 갱신(`MATCH_REFRESH_ENABLED`)은 **로컬 기본 off**다. 로컬도 같은 운영
  RDS에 붙으므로 개발 중 노트북이 배치를 돌리지 않게 막아둔 것 — 실배포에서만 켠다.
- RDS와 OpenSearch가 VPC 내부에 있으면 각각 SSH 터널을 먼저 열어야 한다. 접속 정보와
  비밀값은 `.env`에만 두고 README나 Git에 넣지 않는다.

테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

프론트도 함께 실행할 때는 별도 터미널에서 `bidmate-frontend` 개발 서버를 3000번
포트로 실행한다.

### EC2 서버

RDS에 접근 가능한 프라이빗 EC2에서 Nginx가 8000 포트를 받고 Blue(8001)·
Green(8002) Docker 컨테이너로 전달한다. 전체 CI/CD 절차는
[deploy/CD.md](deploy/CD.md) 참조.

```bash
# EC2 최초 1회
sudo bash deploy/bootstrap-blue-green.sh
```

상태 확인:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/version
sudo docker ps --filter label=com.bidmate.service=api
```

수동 실행이 필요하면: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
(로컬 개발 시엔 `--reload`, DB는 docker Postgres 또는 SSH 터널)

## API

인증이 필요한 경로(🔒)는 `Authorization: Bearer <Cognito ID 토큰>`을 요구한다.
전체 스키마는 `GET /docs`(Swagger).

### 공고
| 메서드 · 경로 | 설명 |
|---|---|
| `GET /bids` | 공고 목록 (category/sort/page, 마감 지난 공고 제외) |
| `GET /bids/{bid_id}` | 공고 상세 + 자격요건 15필드 |
| `GET /bids/search` | 공고 검색 (q / sort=deadline·recent / include_closed) |
| `GET /stats` | 공고 통계 집계 (총계·품목 칩·월별 추세·예산 구간·수요기관 top8, 비로그인) — `bid_stats` matview |

### 회사 🔒
| 메서드 · 경로 | 설명 |
|---|---|
| `GET /me` | 현재 로그인 회사 정보 |
| `DELETE /me` | 회원 탈퇴(소프트 삭제) |
| `GET /me/profile` | 회사 자격요건 프로필 8섹션 조회 |
| `PUT /me/profile` | 프로필 전체 저장(full replace) — 저장 성공 시 그 회사 매칭 재계산 |
| `GET·POST·DELETE /me/scraps` | 공고 스크랩(목록/담기/빼기) |
| `GET /me/matches` | 회사별 공고 매칭 (기본 **추천순**, sort=recommended·deadline·recent). '가능'·'보완가능'만 노출, 마감일 없는 공고는 뒤로 |
| `GET /me/matches/summary` | 매칭 가능 공고 건수(홈 대시보드) |
| `GET /me/recommendations` | 회사별 개인화 추천 |

인력 섹션은 자격·등급에 더해 **분야(`field_family`, D-19)** 를 받는다 — 공고가
(등급×분야×인원)으로 요구하므로 같은 자격도 분야별로 여러 행이 될 수 있다.

### 챗봇 세션 🔒
| 메서드 · 경로 | 설명 |
|---|---|
| `GET /me/sessions` | 내 대화 목록(최근순) |
| `GET /me/sessions/{id}` | 대화 상세(메시지 전체) |
| `DELETE /me/sessions/{id}` | 대화 삭제(소프트) |
| `DELETE /me/sessions/{id}/last-turn` | 마지막 턴 취소 |

### 에이전트 · 이벤트 · 기타
| 메서드 · 경로 | 설명 |
|---|---|
| `POST /agent/chat` | 대화 에이전트 (RAG, Bedrock). 회사당 레이트리밋(분당·동시성·일일) 적용 |
| `POST /events` | 고객 여정 이벤트 수집 (S3 NDJSON 적재) |
| `GET /health` | 헬스체크(liveness) |
| `GET /ready` | 준비 확인(DB `SELECT 1`, 실패 시 503) |
| `GET /version` | 배포 SHA·Blue/Green 슬롯 |
| `GET /metrics` | Prometheus 텍스트 지표(관측) — DB 풀·matview/매칭 신선도. 미인증, 내부망 전용 |
| `GET /docs` | Swagger 문서 |

응답은 **원본값만** 내린다 — D-day 계산·코드 한글변환·금액 포맷은 프론트 담당.

## 관측성 (CloudWatch)

`GET /metrics`(Prometheus 텍스트, 의존성 없이 수동 노출)를 **CloudWatch Agent**가 스크레이프해
CloudWatch로 올린다(namespace `BidMate/Backend`). 같은 에이전트가 컨테이너 로그(`/bidmate/api`)와
호스트 지표(CPU/디스크/메모리, `BidMate/Host`)도 수집한다. **Prometheus 서버는 두지 않고** Grafana는
CloudWatch 데이터소스로 조회한다. 에이전트 설정·설치·알람·Budgets 스크립트는 `deploy/observability/`
(SETUP.md 참조). 핵심 게이지 — DB 풀 사용량, `bid_stats` matview 신선도, `match_results` 갱신 신선도.

## 인증 (Cognito)

로그인/회원가입은 프론트가 Cognito를 직접 호출하고, 서버는 **ID 토큰을 검증**해
`cognito_sub`로 `companies` 행을 찾는다(없으면 최초 `/me` 호출 시 JIT 생성). `/me/*`는
로그인 필수, 공고 조회는 비로그인도 허용한다. 로컬 개발은 `AUTH_DISABLED=true` +
`DEV_COMPANY_ID`로 토큰 없이 고정 회사로 우회(운영에선 절대 금지).

## 마이그레이션 (Alembic)

`bid_table`(파이프라인)·회사 프로필 8테이블·`match_results`(에이전트/배치)처럼 **외부가
소유·적재하는 테이블은 관리 대상에서 제외**하고, API가 직접 만드는 테이블만 관리하는
coexist 모델. ORM은 그 외부 테이블을 **읽기 매핑**만 한다(create_all 호출 안 함). 운영
RDS `stamp`은 소유 팀과 협의 후 1회. 자세한 건 [alembic/README.md](alembic/README.md).

```bash
alembic upgrade head        # 마이그레이션 적용
alembic revision --autogenerate -m "설명"   # 신규 마이그레이션(외부 테이블은 자동 제외)
```

## 배포

PR은 GitHub Actions가 Python 테스트와 ARM64 이미지를 검증한다. `main` CI가 성공하면
OIDC로 ECR에 SHA 이미지를 저장하고 SSM으로 프라이빗 EC2의 비활성 슬롯에 배포한다.
Nginx 전환·스모크 테스트·자동 롤백은 [deploy/CD.md](deploy/CD.md) 참조.

## 규칙

커밋·브랜치 컨벤션은 [Github_Convention.md](Github_Convention.md), 이슈/PR은
`.github/` 템플릿을 따른다.

## 주의

- **외부 소유 테이블**: `bid_table`·`bid_attachments`(파이프라인), 회사 프로필 8테이블은
  이 서버가 **읽기 매핑**만 한다. 스키마 변경(DDL)은 소유 측 + 팀 협의로만.
- `companies`·`company_bid_scraps`·`chat_sessions`·`chat_messages`·`chat_daily_usage`는
  API가 쓰기(Alembic 관리). 회사 프로필 8테이블은 외부 소유라 **읽기/쓰기(DML)는 하되 DDL은 안 냄**.
- `match_results`는 **스키마는 외부 소유(DDL 금지)지만 적재는 API가 한다** — 자격 저장
  훅(#75)과 주기 갱신 스케줄러(#80)가 DB 함수 `compute_match_results()` 결과로 채운다.
  계산 로직은 그 DB 함수에 있고 이 리포에 없다.
- 회사 데이터는 `company_id`로 격리(멀티테넌시). 공고 원본은 공용.
- 대화 에이전트는 **별도 서비스**다(루프백 8010). 백엔드는 `POST {AGENT_BASE_URL}/turn`으로
  호출하고(`app/agents/agent_client.py`), 대화·세션 컨텍스트는 RDS에 보관한다(ADR-22).
  `bidmate-agents` 의존성은 요청/응답 **계약(`agents.schemas`)** 공유용으로만 남아 있다.
  Bedrock·OpenSearch·Cloudflare 접속 설정은 이제 에이전트 쪽 런타임 몫이다
  (`.env`는 두 배포가 공유 — `deploy/env.ec2.example` 참고).
- 에이전트 버전은 `app/requirements.txt`에 **커밋 SHA로 고정**(재현성) — 반영은 그 SHA를
  올리는 PR로 한다(에이전트 main 머지 시 자동 범프 PR을 여는 리시버 워크플로 있음:
  `.github/workflows/agent-sync.yml`). CI가 새 에이전트로 pytest·스모크를 돌려 검증한다.
- **운영 슬롯 부팅 가드**: 운영 배포는 Cognito·CORS(실 오리진)가 설정돼야 기동한다 —
  미설정이면 서버가 뜨지 않고 Blue/Green이 자동 롤백한다(조용한 미스컨피그 방지).
