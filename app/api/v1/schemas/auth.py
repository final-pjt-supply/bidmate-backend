# -*- coding: utf-8 -*-
"""인증 응답 스키마.

비밀번호·토큰은 절대 내리지 않는다(Cognito 소관). 프론트가 필요한 건 '내가 누구이고
어느 회사인가'뿐이다.
"""
from pydantic import BaseModel


class MeResponse(BaseModel):
    """로그인한 사용자의 회사 정보."""

    company_id: str
    email: str | None = None
    name: str | None = None
    biz_reg_no: str | None = None
