# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 라우터 — 로그인한 회사가 자기 프로필을 조회한다.

/me 하위에 둔다: 대상이 '내 회사'다(company_id는 토큰에서 오고 요청으로 안 받는다).
읽기전용 — 입력/수정(PUT)은 후속 이슈.
"""
from fastapi import APIRouter, Depends

from app.api.deps import (
    CurrentUser,
    get_authenticated_user,
    get_company_profile_service,
)
from app.api.v1.schemas.profile import ProfileResponse
from app.services.company_profile_service import CompanyProfileService

router = APIRouter(prefix="/me/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
def get_profile(
    service: CompanyProfileService = Depends(get_company_profile_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> ProfileResponse:
    """내 자격요건 프로필 — 8개 섹션. 미입력이면 빈 섹션으로 200."""
    return service.get_profile(company_id=int(current_user.company_id))
