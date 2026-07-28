# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 라우터 — 로그인한 회사가 자기 프로필을 조회/저장한다.

/me 하위에 둔다: 대상이 '내 회사'다(company_id는 토큰에서 오고 요청으로 안 받는다).
GET=조회, PUT=전체 저장(회원가입 폼). 모든 섹션은 선택이다.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    CurrentUser,
    get_authenticated_user,
    get_company_profile_service,
    get_match_service,
)
from app.api.v1.schemas.profile import ProfileResponse, ProfileUpsertRequest
from app.services.company_profile_service import (
    CompanyProfileService,
    ProfileValidationError,
)
from app.services.match_service import MatchService

logger = logging.getLogger(__name__)

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
    match_service: MatchService = Depends(get_match_service),
    current_user: CurrentUser = Depends(get_authenticated_user),
) -> ProfileResponse:
    """내 자격요건 프로필 전체 저장(full replace).

    클라는 code만 보내고 서버가 마스터에서 name을 채운다. 모든 섹션 선택 —
    빈 프로필도 저장된다. 마스터에 없는 코드/섹션 내 코드 중복은 422.

    저장이 성공하면 그 회사 매칭(match_results)을 재계산한다 — 자격이 바뀌면
    판정도 바뀌므로 즉시 반영해 다음 배치를 기다리지 않게 한다. 재계산 실패는
    저장을 되돌리지 않는다(로그만 남기고 200) — 저장은 이미 커밋됐고, 매칭은
    일배치가 다시 채운다.
    """
    company_id = int(current_user.company_id)
    try:
        result = service.save_profile(company_id=company_id, payload=payload)
    except ProfileValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail
        )

    try:
        match_service.recompute_for_company(company_id)
    except Exception:
        logger.exception(
            "자격 저장 후 매칭 재계산 실패 — 저장은 유지(company_id=%s)", company_id
        )

    return result
