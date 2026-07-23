# BidMate Backend

나라장터(조달청) 공고를 조회·서빙하는 **읽기 중심 API 서버**. 수집·추출 파이프라인이
채워둔 데이터를 프론트/에이전트에 내려주고, 향후 회사 자격 매칭까지 담당한다.

> 데이터 파이프라인(수집→추출→임베딩→적재)은 별도 리포에 있다. 이 리포는
> **그 결과(bid_table 등)를 읽어 서빙하는 API 계층**이다.

## 기술 스택

FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL(주) · OpenSearch(보조, 예정)
· Cognito JWT(예정) · Mangum + Lambda(배포 목표) · pytest · Docker

## 구조 (모듈러 모놀리스, 의존성 단방향)

```
app/
  api/        FastAPI 라우터·의존성·응답 스키마(DTO)
  services/   비즈니스 로직(정책·조립·페이징)
  agents/     비동기 에이전트(예정)
  domain/     도메인 모델·enum (추출 파이프라인과 공유)
  infra/      DB 세션·ORM·repository
  config.py   설정(.env 로딩)
  main.py     앱 진입점 + Lambda 핸들러
tests/api/    계약 테스트 + repository 통합 테스트
alembic/      마이그레이션 (API 소유 테이블만 관리)
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
curl http://<EC2_PUBLIC_IP>:8000/health # 상태 확인
```

수동 실행이 필요하면: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
(로컬 개발 시엔 `--reload`, DB는 docker Postgres 또는 SSH 터널)

## API

| 메서드 · 경로 | 설명 |
|---|---|
| `GET /bids` | 공고 목록 (category/sort/page, 마감 지난 공고 제외) |
| `GET /bids/{bid_id}` | 공고 상세 + 자격요건 15필드 |
| `GET /health` | 헬스체크 |
| `GET /docs` | Swagger 문서 |

응답은 **원본값만** 내린다 — D-day 계산·코드 한글변환·금액 포맷은 프론트 담당.

## 마이그레이션 (Alembic)

bid_table 등 **파이프라인 소유 테이블은 제외**하고, API가 새로 만드는 테이블만 관리하는
coexist 모델. 운영 RDS `stamp`은 파이프라인 팀과 협의 후 1회. 자세한 건 [alembic/README.md](alembic/README.md).

```bash
alembic upgrade head        # 마이그레이션 적용
alembic revision --autogenerate -m "설명"   # 신규 마이그레이션
```

## 배포

개발용 EC2(uvicorn/systemd) 절차는 [deploy/README.md](deploy/README.md) 참조. 정식 배포
(Lambda + API Gateway)는 이후 단계.

## 규칙

커밋·브랜치 컨벤션은 [Github_Convention.md](Github_Convention.md), 이슈/PR은
`.github/` 템플릿을 따른다.

## 주의

- `bid_table`·`bid_attachments`는 **파이프라인 소유 → 이 서버는 읽기 전용.** DDL/쓰기 금지.
- 회사 데이터는 `company_id`로 격리(멀티테넌시). 공고 원본은 공용.
