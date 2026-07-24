# -*- coding: utf-8 -*-
"""공고 조회 라우터. HTTP 경계만 담당한다 — 쿼리 파싱/검증(Enum·범위)은 FastAPI에,
정책/조립은 service에, 쿼리는 repository에 위임한다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, get_bid_service, get_current_user
from app.api.v1.schemas.bid import BidDetail, BidListResponse
from app.domain.enums import BidCategory, BidSortKey, SearchSortKey
from app.services.bid_service import BidNotFoundError, BidService

router = APIRouter(prefix="/bids", tags=["bids"])


@router.get("", response_model=BidListResponse)
def list_bids(
    category: BidCategory | None = Query(
        default=None, description="업무구분 필터. 없으면 전체. 잘못된 값은 422."
    ),
    sort: BidSortKey = Query(
        default=BidSortKey.DEADLINE,
        description="정렬. score는 현재 deadline으로 폴백(match_score 미구현).",
    ),
    today: bool = Query(default=False, description="true면 오늘(KST) 신규 공고만."),
    page: int = Query(default=1, ge=1, description="1-based. 범위 밖이면 빈 배열."),
    service: BidService = Depends(get_bid_service),
    current_user: CurrentUser = Depends(get_current_user),   # ★ 인증/회사별정렬 진입점
) -> BidListResponse:
    return service.list_bids(
        category=category,
        sort=sort,
        today=today,
        page=page,
        company_id=current_user.company_id,
    )


@router.get("/search", response_model=BidListResponse)
def search_bids(
    category: BidCategory | None = Query(
        default=None, description="업무구분 필터. 없으면 전체. 잘못된 값은 422."
    ),
    sort: SearchSortKey = Query(
        default=SearchSortKey.DEADLINE,
        description="정렬. deadline=마감 임박순, recent=최신 등록순. score는 받지 않는다.",
    ),
    q: str | None = Query(
        default=None,
        max_length=100,
        description="공고명·수요기관·공고기관 부분일치(대소문자 무시). 공백만 주면 미적용.",
    ),
    page: int = Query(default=1, ge=1, description="1-based. 범위 밖이면 빈 배열."),
    service: BidService = Depends(get_bid_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> BidListResponse:
    """공고 검색. 마감 지난 공고는 제외한다.

    ⚠ 이 라우트는 반드시 아래 `/{bid_id}`보다 위에 있어야 한다. 아래로 내려가면
    FastAPI가 정의 순서대로 매칭하므로 "search"가 bid_id로 먹혀 404가 난다.
    """
    _ = current_user   # 인증 게이트만 통과시킨다(검색은 아직 회사별 분기 없음).
    return service.search_bids(category=category, sort=sort, page=page, q=q)


@router.get("/{bid_id}", response_model=BidDetail)
def get_bid(
    bid_id: str,
    service: BidService = Depends(get_bid_service),
    current_user: CurrentUser = Depends(get_current_user),   # ★ 인증/회사별정렬 진입점
) -> BidDetail:
    try:
        return service.get_bid(bid_id, company_id=current_user.company_id)
    except BidNotFoundError:
        # 미노출(비-merged)과 '없음'을 동일하게 404로 통일 — 존재 여부를 흘리지 않는다.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bid not found"
        )
