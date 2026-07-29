# -*- coding: utf-8 -*-
"""FastAPI 앱 + Lambda 진입점(Mangum).

배포는 EC2 uvicorn(상시 프로세스). 이벤트 S3 sink는 인메모리 버퍼 + 주기 flush라
상시 프로세스를 전제한다(Lambda로 가면 sink 대신 Firehose). 로컬은
`uvicorn app.main:app --reload`.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from sqlalchemy import text

from app.api.v1.router import api_router
from app.config import get_settings
from app.infra.db.session import engine

_settings = get_settings()


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


app = FastAPI(title="BidMate API", version="0.1.0", lifespan=lifespan)

# 프론트(브라우저)가 다른 오리진에서 호출하므로 CORS 허용이 필수다. 없으면 서버가
# 정상 응답해도 브라우저가 막는다. 허용 오리진은 설정(cors_origins, .env로 주입)에서
# 온다 — 와일드카드(*) 대신 명시 목록을 써서 아무 사이트나 못 부르게 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


# AWS Lambda 핸들러(template.yaml에서 참조).
handler = Mangum(app)
