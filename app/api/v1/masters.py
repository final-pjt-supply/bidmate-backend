# -*- coding: utf-8 -*-
"""마스터 자동완성 라우터 — 회사정보 폼의 품목 선택기가 쓴다.

로그인 필수: 소비처가 마이페이지 폼뿐이고, 35,171행짜리 참조 데이터를 익명에게
열어 둘 이유가 없다(대량 수집 방지).
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, get_authenticated_user, get_master_service
from app.api.v1.schemas.master import ItemSearchResponse
from app.services.master_service import MasterService

router = APIRouter(prefix="/masters", tags=["masters"])


@router.get("/items", response_model=ItemSearchResponse)
def search_items(
    q: str = Query(default="", description="품명 부분일치. 비면 빈 목록."),
    limit: int = Query(default=20, ge=1, le=50),
    service: MasterService = Depends(get_master_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> ItemSearchResponse:
    """품목 자동완성 — 10자리(세부품명)만. 8자리 물품분류는 노출하지 않는다."""
    return service.search_items(q=q, limit=limit)
