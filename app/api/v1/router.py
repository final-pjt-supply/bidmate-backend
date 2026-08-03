# -*- coding: utf-8 -*-
"""라우터 집약. 엔드포인트가 늘면 여기서 include_router로 묶는다.

URL에는 버전 프리픽스를 붙이지 않는다 — 프론트 확정 명세가 /bids 이기 때문이다.
'v1'은 코드 구성(디렉토리)용 이름일 뿐이다. 추후 버저닝이 필요하면 여기 prefix만
'/v1'로 바꾼다.
"""
from fastapi import APIRouter

from app.api.v1 import (
    agents,
    auth,
    bids,
    events,
    masters,
    matches,
    profile,
    recommendations,
    scraps,
    sessions,
    stats,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(bids.router)
api_router.include_router(events.router)
api_router.include_router(agents.router)
api_router.include_router(scraps.router)
api_router.include_router(profile.router)
api_router.include_router(matches.router)
api_router.include_router(recommendations.router)
api_router.include_router(masters.router)
api_router.include_router(sessions.router)
api_router.include_router(stats.router)
