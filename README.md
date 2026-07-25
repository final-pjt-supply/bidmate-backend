# BidMate Backend

나라장터(조달청) 공고를 **조회·서빙**하고, **대화 에이전트**와 **회사 자격 매칭**을
제공하는 API 서버. 수집·추출 파이프라인이 채워둔 데이터를 프론트/에이전트에 내려준다.

> 데이터 파이프라인(수집→추출→임베딩→적재)은 별도 리포에 있다. 이 리포는
> **그 결과(bid_table 등)를 읽어 서빙하는 API 계층**이다.

## 기술 스택

FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL(주) · Cognito JWT(인증)
· AWS Bedrock(대화 에이전트) · OpenSearch(에이전트 검색) · S3(이벤트 적재)
· Mangum + Lambda(배포 목표) · pytest · Docker

## 구조 (모듈러 모놀리스, 의존성 단방향)

```
app/
  api/        FastAPI 라우터·의존성·응답 스키마(DTO)
  services/   비즈니스 로직(정책·조립·페이징)
  agents/     대화 에이전트 연결 (ADR 0005: bidmate-agents 패키지를 같은 프로세스에 임베드)
  domain/     도메인 모델·enum (추출 파이프라인과 공유)
  infra/      DB 세션·ORM·repository / auth(Cognito 검증) / s3(이벤트 싱크)
  config.py   설정(.env 로딩)
  main.py     앱 진입점 + Lambda 핸들러
tests/api/    계약 테스트 + repository 통합 테스트
alembic/      마이그레이션 (API 소유 테이블만 관리, coexist)
deploy/       개발용 EC2 배포 산출물
```

## 실행 (서버)

RDS에 접근 가능한 EC2(RDS와 같은 VPC)에서 **systemd 상시 실행**. EC2 스펙·보안그룹·코드
업로드 등 전체 절차는 [deploy/README.md](deploy/README.md) 참조.

```bash
# EC2에서 (코드 업로드 후)
cp deploy/env.ec2.example .env    # RDS 직접접속 정보 채우기 (SSH 터널 아님)
bash deploy/setup.sh              # venv + 의존성 + systemd 등록 + 헬스체크 원샷
```

서비스 관리:

```bash
sudo systemctl restart bidmate-api      # 코드 갱신 후 재배포
journalctl -u bidmate-api -f            # 로그
curl http://localhost:8000/health       # 상태 확인
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

### 회사 🔒
| 메서드 · 경로 | 설명 |
|---|---|
| `GET /me` | 현재 로그인 회사 정보 |
| `DELETE /me` | 회원 탈퇴(소프트 삭제) |
| `GET /me/profile` | 회사 자격요건 프로필 8섹션 조회 |
| `GET·POST·DELETE /me/scraps` | 공고 스크랩(목록/담기/빼기) |
| `GET /me/matches` | 회사별 공고 매칭 결과 (verdict·근거, sort=deadline·recent) |

### 에이전트 · 이벤트 · 기타
| 메서드 · 경로 | 설명 |
|---|---|
| `POST /agent/chat` | 대화 에이전트 (RAG, Bedrock) |
| `POST /events` | 고객 여정 이벤트 수집 (S3 NDJSON 적재) |
| `GET /health` | 헬스체크 |
| `GET /docs` | Swagger 문서 |

응답은 **원본값만** 내린다 — D-day 계산·코드 한글변환·금액 포맷은 프론트 담당.

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

개발용 EC2(uvicorn/systemd) 절차는 [deploy/README.md](deploy/README.md) 참조. 정식 배포
(Lambda + API Gateway 또는 컨테이너 CI/CD)는 이후 단계.

## 규칙

커밋·브랜치 컨벤션은 [Github_Convention.md](Github_Convention.md), 이슈/PR은
`.github/` 템플릿을 따른다.

## 주의

- **외부 소유 테이블**: `bid_table`·`bid_attachments`(파이프라인), 회사 프로필 8테이블·
  `match_results`(에이전트/배치)는 이 서버가 **읽기 매핑**만 한다. 스키마 변경(DDL)은
  소유 측 + 팀 협의로만.
- `companies`·`company_bid_scraps`는 API가 쓰기(가입 JIT 생성·탈퇴·스크랩).
- 회사 데이터는 `company_id`로 격리(멀티테넌시). 공고 원본은 공용.
- 대화 에이전트는 `bidmate-agents`를 같은 프로세스에 임베드(ADR 0005). Bedrock·OpenSearch·
  Cloudflare 접속 설정은 `.env`로 주입한다.
```
