# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 라우터 — 로그인한 회사가 자기 프로필을 조회/저장한다.

/me 하위에 둔다: 대상이 '내 회사'다(company_id는 토큰에서 오고 요청으로 안 받는다).
GET=조회, PUT=전체 저장(회원가입 폼). 모든 섹션은 선택이다.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    CurrentUser,
    get_authenticated_user,
    get_company_profile_service,
)
from app.api.v1.schemas.profile import ProfileResponse, ProfileUpsertRequest
from app.services.company_profile_service import (
    CompanyProfileService,
    ProfileValidationError,
)

router = APIRouter(prefix="/me/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
def get_profile(
    service: CompanyProfileService = Depends(get_company_profile_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> ProfileResponse:
    """내 자격요건 프로필 — 8개 섹션. 미입력이면 빈 섹션으로 200."""
    return service.get_profile(company_id=int(current_user.company_id))


@router.put("", response_model=ProfileResponse)
def upsert_profile(
    payload: ProfileUpsertRequest,
    service: CompanyProfileService = Depends(get_company_profile_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> ProfileResponse:
    """내 자격요건 프로필 전체 저장(full replace).

    클라는 code만 보내고 서버가 마스터에서 name을 채운다. 모든 섹션 선택 —
    빈 프로필도 저장된다. 마스터에 없는 코드/섹션 내 코드 중복은 422.
    """
    try:
        return service.save_profile(
            company_id=int(current_user.company_id), payload=payload
        )
    except ProfileValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail
        )
