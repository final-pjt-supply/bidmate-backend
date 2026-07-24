# -*- coding: utf-8 -*-
"""인증 관련 엔드포인트.

로그인/회원가입 자체는 Cognito가 처리하므로 여기 없다(프론트가 Cognito를 직접 호출).
서버는 '토큰이 유효한지'와 '그 사용자의 회사가 누구인지'만 답한다.

/me는 프론트가 로그인 직후 호출해 company_id를 확보하는 용도이며, 이 시점에
companies 행이 없으면 JIT로 생성된다(deps.get_current_user 참조).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_authenticated_user, get_db
from app.api.v1.schemas.auth import MeResponse
from app.infra.db.repositories.company_repository import CompanyRepository

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeResponse, summary="Get Current Company")
def get_me(
    current_user: CurrentUser = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    company = CompanyRepository(db).get_by_id(current_user.company_id)
    if company is None:
        # 토큰은 유효한데 회사 행이 사라진 경우(수동 삭제 등).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="회사 정보를 찾을 수 없습니다"
        )
    return MeResponse(
        company_id=str(company.id),
        email=company.email,
        name=company.name,
        biz_reg_no=company.biz_reg_no,
    )
