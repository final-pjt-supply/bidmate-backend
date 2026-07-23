# -*- coding: utf-8 -*-
"""FastAPI 앱 + Lambda 진입점(Mangum).

배포는 Lambda + Mangum(stateless)라 세션/전역 가변 상태에 의존하지 않는다. 로컬은
`uvicorn app.main:app --reload`로 띄운다.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

from app.api.v1.router import api_router
from app.config import get_settings

app = FastAPI(title="BidMate API", version="0.1.0")

# 프론트(브라우저)가 다른 오리진에서 호출하므로 CORS 허용이 필수다. 없으면 서버가
# 정상 응답해도 브라우저가 막는다. 허용 오리진은 설정(cors_origins, .env로 주입)에서
# 온다 — 와일드카드(*) 대신 명시 목록을 써서 아무 사이트나 못 부르게 한다.
_settings = get_settings()
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
    return {"status": "ok"}


# AWS Lambda 핸들러(template.yaml에서 참조).
handler = Mangum(app)
