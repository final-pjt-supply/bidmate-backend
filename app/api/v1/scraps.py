# -*- coding: utf-8 -*-
"""스크랩 라우터 — 로그인한 회사가 공고를 담고/빼고/모아본다.

/me 하위에 둔다: 조회 대상이 '내 회사'다(company_id는 토큰에서 온다, 요청으로 안 받음).
목록 응답은 /bids와 같은 BidListResponse라 프론트가 BidCard를 그대로 쓴다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, get_authenticated_user, get_scrap_service
from app.api.v1.schemas.bid import BidListResponse
from app.api.v1.schemas.scrap import ScrapCreate
from app.services.scrap_service import BidNotFoundError, ScrapService

router = APIRouter(prefix="/me/scraps", tags=["scraps"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
def add_scrap(
    payload: ScrapCreate,
    service: ScrapService = Depends(get_scrap_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> Response:
    """담기. 이미 담겼어도 204(멱등). 존재하지 않는 공고면 404."""
    try:
        service.add(company_id=int(current_user.company_id), bid_id=payload.bid_id)
    except BidNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bid not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{bid_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_scrap(
    bid_id: str,
    service: ScrapService = Depends(get_scrap_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> Response:
    """빼기. 담겨있지 않아도 204(멱등)."""
    service.remove(company_id=int(current_user.company_id), bid_id=bid_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("", response_model=BidListResponse)
def list_scraps(
    page: int = Query(default=1, ge=1, description="1-based. 범위 밖이면 빈 배열."),
    service: ScrapService = Depends(get_scrap_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> BidListResponse:
    """내 스크랩 목록 — 담은 최신순. 마감 공고도 포함한다."""
    return service.list_scraps(company_id=int(current_user.company_id), page=page)
