# -*- coding: utf-8 -*-
"""회사별 개인화 추천 조회."""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import (
    CurrentUser,
    get_authenticated_user,
    get_recommendation_service,
)
from app.api.v1.schemas.recommendation import RecommendationListResponse
from app.infra.search.recommendation_search import RecommendationSearchError
from app.services.recommendation_service import RecommendationService

router = APIRouter(prefix="/me/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationListResponse)
def list_recommendations(
    limit: int = Query(default=10, ge=1, le=30),
    service: RecommendationService = Depends(get_recommendation_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> RecommendationListResponse:
    """자격 가능한 미마감 공고를 회사 관심도 순으로 재정렬한다."""
    try:
        return service.recommend(
            company_id=int(current_user.company_id),
            limit=limit,
        )
    except RecommendationSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
