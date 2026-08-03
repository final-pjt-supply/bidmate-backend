# -*- coding: utf-8 -*-
"""공고 통계 라우터. HTTP 경계만 담당한다.

비로그인 조회 가능 — 개별 공고가 아니라 집계값이고, 회사별로 달라지지 않는다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_stats_service
from app.api.v1.schemas.stats import StatsResponse
from app.domain.enums import StatsCategory
from app.services.stats_service import StatsService, StatsUnavailableError

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
def get_stats(
    category: StatsCategory = Query(
        default=StatsCategory.ALL,
        description="업종. 기본은 업종 무관 전체. 잘못된 값은 422.",
    ),
    tag: str | None = Query(
        default=None,
        description="품목 태그. 응답 tags 밖의 값이면 전체로 폴백한다(404 아님).",
    ),
    service: StatsService = Depends(get_stats_service),
) -> StatsResponse:
    """조건별 집계 — 총계·품목 칩·월별 추세·예산 구간·수요기관 상위 8개.

    값은 bid_stats matview에서 그대로 나온다. 진행 중인 달은 모든 집계에서
    빠져 있고(`period` 참조), 갱신 시각은 `computed_at`에 담긴다.
    """
    try:
        return service.get_stats(category=category, tag=tag)
    except StatsUnavailableError as exc:
        # 집계가 아직 만들어지지 않았다. 빈 값을 200으로 주면 화면이 "공고 0건"이라는
        # 거짓을 그린다 — 데이터가 없는 것과 아직 계산되지 않은 것은 다르다.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="통계를 준비하고 있어요. 잠시 후 다시 시도해 주세요.",
        ) from exc
