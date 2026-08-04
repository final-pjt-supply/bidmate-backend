# -*- coding: utf-8 -*-
"""매칭 조회 라우터 — 로그인한 회사의 공고 매칭 결과를 모아본다.

/me 하위: 대상이 '내 회사'다(company_id는 토큰에서). match_results(배치 사전계산)를
공고정보와 함께 내린다. 마감 전 + merged 공고만, 마감임박순/최신순 정렬.
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, get_authenticated_user, get_match_service
from app.api.v1.schemas.match import MatchListResponse, MatchSummaryResponse
from app.domain.enums import MatchSortKey
from app.services.match_service import MatchService

router = APIRouter(prefix="/me/matches", tags=["matches"])


@router.get("", response_model=MatchListResponse)
def list_matches(
    sort: MatchSortKey = Query(
        default=MatchSortKey.RECOMMENDED,
        description="recommended(추천순=자격 충족 비율, 기본)/deadline(마감임박)/recent(최신)",
    ),
    page: int = Query(default=1, ge=1, description="1-based. 범위 밖이면 빈 배열."),
    include_infeasible: bool = Query(
        default=False,
        description="기본은 '가능'·'보완가능'만. true면 확인필요·불가·판정없음도 포함.",
    ),
    service: MatchService = Depends(get_match_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> MatchListResponse:
    """내 공고 매칭 목록 — merged·마감전만, 사전계산 verdict/근거 포함.

    기본 정렬은 추천순(자격 충족 비율 높은 순). 기본 노출은 참가 가치가 있는
    '가능'·'보완가능'만 — 확인필요(공고측 데이터 미해석)·불가·판정없음은 뺀다. 전체가
    필요하면 include_infeasible=true.
    """
    return service.list_matches(
        company_id=int(current_user.company_id),
        sort=sort,
        page=page,
        include_infeasible=include_infeasible,
    )


@router.get("/summary", response_model=MatchSummaryResponse)
def match_summary(
    service: MatchService = Depends(get_match_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> MatchSummaryResponse:
    """홈 대시보드 건수 — 목록 없이 카운트만.

    홈은 total·urgent만 쓰는데 목록을 부르면 공고 20건을 받아 버리게 된다.
    """
    return service.summary(company_id=int(current_user.company_id))
