# -*- coding: utf-8 -*-
"""FastAPI 앱 + Lambda 진입점(Mangum).

배포는 EC2 uvicorn(상시 프로세스). 이벤트 S3 sink는 인메모리 버퍼 + 주기 flush라
상시 프로세스를 전제한다(Lambda로 가면 sink 대신 Firehose). 로컬은
`uvicorn app.main:app --reload`.
"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from sqlalchemy import text

from app.api.v1.router import api_router
from app.config import get_settings
from app.infra.db.session import engine

_settings = get_settings()
_request_logger = logging.getLogger("app.request")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이벤트 S3 sink의 주기 flush 스레드 기동/정리. s3_bucket이 설정된 실배포에서만
    # 켠다(로컬/테스트는 미설정이라 boto3 로드·스레드 없이 통과).
    sink = None
    if _settings.s3_bucket:
        from app.infra.s3.event_sink import get_event_sink

        sink = get_event_sink()
        sink.start()

    # 매칭 주기 갱신(#80). 신규 공고를 match_results에 반영하는 주체가 여기뿐이라
    # 끄면 회원이 프로필을 다시 저장할 때까지 새 공고가 추천에 안 나온다.
    # 기본 off — 로컬/테스트가 운영 RDS에 배치를 돌리지 않게(실배포 .env에서 켠다).
    refresher = None
    if _settings.match_refresh_enabled:
        import asyncio

        from app.infra.db.session import SessionLocal
        from app.services.match_refresh import match_refresh_loop

        refresher = asyncio.create_task(
            match_refresh_loop(
                SessionLocal,
                interval_sec=_settings.match_refresh_interval_sec,
                full_hour=_settings.match_refresh_full_hour,
            )
        )
    try:
        yield
    finally:
        if refresher is not None:
            refresher.cancel()
        if sink is not None:
            sink.stop()
        # 추천 검색 어댑터가 들고 있는 httpx 커넥션 풀 정리. 한 번도 안 쓰였으면
        # 어댑터를 만들지 않는다(lru_cache에 값이 없으면 새로 만들 필요가 없다).
        from app.api.deps import get_recommendation_search

        if get_recommendation_search.cache_info().currsize:
            get_recommendation_search().close()


app = FastAPI(title="BidFriend API", version="0.1.0", lifespan=lifespan)

# 프론트(브라우저)가 다른 오리진에서 호출하므로 CORS 허용이 필수다. 없으면 서버가
# 정상 응답해도 브라우저가 막는다. 허용 오리진은 설정(cors_origins, .env로 주입)에서
# 온다 — 와일드카드(*) 대신 명시 목록을 써서 아무 사이트나 못 부르게 한다.
#
# allow_credentials=False: 인증은 JWT를 Authorization 헤더로 싣는다(쿠키/세션 아님)
# → credentials 모드가 불필요하다. True로 두면 운영자가 CORS_ORIGINS=*를 넣었을 때
# Starlette이 요청 Origin을 그대로 반사하며 credentials까지 허용하는 풋건만 넓힌다(QA M5).
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """요청 관측성 — 상관관계 ID + 경로·상태·소요시간 로깅.

    백엔드에 APM이 없어 p95·병목이 안 보인다. 이후 성능 결정(추천 캐시·프로필 저장
    비동기화 등)을 감이 아니라 숫자로 하기 위한 최소 계기판이다. 응답의 X-Request-ID로
    클라이언트 오류 신고와 서버 로그를 연결한다.
    """
    request_id = uuid.uuid4().hex
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    _request_logger.info(
        "%s %s %d %.1fms rid=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


app.include_router(api_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """프로세스 생존 확인. 외부 의존성을 조회하지 않는 liveness probe."""
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
def ready() -> dict:
    """요청 처리 준비 확인. 운영 DB에 읽기 전용 SELECT 1을 수행한다."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        # 접속정보나 내부 예외는 응답에 노출하지 않는다.
        raise HTTPException(
            status_code=503, detail="데이터베이스 연결을 확인할 수 없습니다"
        ) from exc
    return {"status": "ready", "database": "ok"}


@app.get("/version", tags=["meta"])
def version() -> dict:
    """현재 트래픽을 처리하는 이미지 버전과 Blue/Green 슬롯."""
    return {
        "version": _settings.app_version,
        "slot": _settings.deployment_slot,
    }


def _age_seconds(sql: str) -> float:
    """DB에서 나이(초)를 구한다. 접속 실패·행 없음·NULL이면 -1(폴백)."""
    try:
        with engine.connect() as connection:
            row = connection.execute(text(sql)).first()
        return round(float(row[0]), 1) if row and row[0] is not None else -1
    except Exception:
        return -1


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus 텍스트 노출(관측). 의존성 없이 핵심 게이지만 수동 노출한다.

    - up / build_info: 프로세스 생존·배포 버전·슬롯
    - db_pool_*: 커넥션 풀 사용량(외부호출 캐스케이드 가시성, ADR-31)
    - bid_stats_age_seconds: matview 신선도 — 외부 Airflow DAG가 멈추면 늘어남(TS-25)
    - match_results_age_seconds: 매칭 주기 갱신이 도는지

    ⚠ 미인증 스크레이프 경로. 백엔드는 프라이빗 VPC라 내부에서만 도달한다
      (공개 노출 시 nginx에서 내부망만 허용할 것).
    """
    # CloudWatch 에이전트의 prometheus 스크레이퍼는 각 지표에 `# TYPE` 메타데이터를
    # 요구한다(없으면 "NO metaData"로 드롭). Prometheus 서버는 관대하나 CW는 엄격하다.
    def gauge(name: str, value, labels: str = "") -> list[str]:
        return [f"# TYPE {name} gauge", f"{name}{labels} {value}"]

    lines: list[str] = []
    lines += gauge("bidmate_up", 1)
    lines += gauge(
        "bidmate_build_info",
        1,
        f'{{version="{_settings.app_version}",slot="{_settings.deployment_slot}"}}',
    )
    pool = engine.pool
    for name, getter in (
        ("bidmate_db_pool_size", getattr(pool, "size", None)),
        ("bidmate_db_pool_checked_out", getattr(pool, "checkedout", None)),
        ("bidmate_db_pool_overflow", getattr(pool, "overflow", None)),
    ):
        try:
            lines += gauge(name, getter())
        except Exception:
            lines += gauge(name, -1)
    lines += gauge(
        "bidmate_bid_stats_age_seconds",
        _age_seconds(
            "SELECT EXTRACT(EPOCH FROM (now() - computed_at)) FROM bid_stats LIMIT 1"
        ),
    )
    lines += gauge(
        "bidmate_match_results_age_seconds",
        # match_results.computed_at은 KST naive(모델 주석)라 now()(UTC)와 직접 빼면
        # 9시간 음수가 된다 — KST 벽시계로 맞춰 비교한다.
        _age_seconds(
            "SELECT EXTRACT(EPOCH FROM (timezone('Asia/Seoul', now()) - max(computed_at))) "
            "FROM match_results"
        ),
    )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# AWS Lambda 핸들러(template.yaml에서 참조).
handler = Mangum(app)
