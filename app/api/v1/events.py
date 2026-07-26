# -*- coding: utf-8 -*-
"""POST /events — 고객 여정 이벤트 수집.

선택적 인증(로그인 전 이벤트도 받아야 하므로 인증을 강제하지 않는다). 토큰이 있으면
company_id를 채우고, 없으면 null로 저장한다(비회원 이벤트). best-effort 성격이라
정상 저장 시 202로 빠르게 응답한다(본문 없음).

⚠ 이전엔 인증 자체를 안 받아 company_id가 항상 null이었다(#61). 로그인 상태에서
발생한 login_completed·profile_updated조차 회사를 특정 못 해, 회사별 행동 집계가
전부 불가능했다. get_authenticated_user(필수)가 아니라 get_current_user(선택)를
쓴다 — home_viewed 등 비로그인 이벤트를 계속 받아야 하므로.
"""
from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, get_current_user, get_event_service
from app.api.v1.schemas.event import EventIn
from app.services.event_service import EventService

router = APIRouter(tags=["events"])


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
def collect_event(
    payload: EventIn,
    service: EventService = Depends(get_event_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    company_id = int(current_user.company_id) if current_user.company_id else None
    service.record(payload, company_id=company_id)
