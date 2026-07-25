# -*- coding: utf-8 -*-
"""매칭 조회 라우터 — 로그인한 회사의 공고 매칭 결과를 모아본다.

/me 하위: 대상이 '내 회사'다(company_id는 토큰에서). match_results(배치 사전계산)를
공고정보와 함께 내린다. 마감 전 + merged 공고만, 마감임박순/최신순 정렬.
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, get_authenticated_user, get_match_service
from app.api.v1.schemas.match import MatchListResponse
from app.domain.enums import SearchSortKey
from app.services.match_service import MatchService

router = APIRouter(prefix="/me/matches", tags=["matches"])


@router.get("", response_model=MatchListResponse)
def list_matches(
    sort: SearchSortKey = Query(
        default=SearchSortKey.DEADLINE, description="deadline(마감임박, 기본)/recent(최신)"
    ),
    page: int = Query(default=1, ge=1, description="1-based. 범위 밖이면 빈 배열."),
    service: MatchService = Depends(get_match_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> MatchListResponse:
    """내 공고 매칭 목록 — merged·마감전만, 사전계산 verdict/근거 포함."""
    return service.list_matches(
        company_id=int(current_user.company_id), sort=sort, page=page
    )
