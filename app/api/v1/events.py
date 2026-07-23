# -*- coding: utf-8 -*-
"""POST /events — 고객 여정 이벤트 수집.

무인증(로그인 전 이벤트를 받아야 하므로 인증을 강제하지 않는다). company_id는 지금
서버가 정할 수 없어(인증 스텁) null로 저장되고, 인증(Phase 2)이 붙으면 선택적 인증에서
채운다. best-effort 성격이라 정상 저장 시 202로 빠르게 응답한다(본문 없음).
"""
from fastapi import APIRouter, Depends, status

from app.api.deps import get_event_service
from app.api.v1.schemas.event import EventIn
from app.services.event_service import EventService

router = APIRouter()


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
def collect_event(
    payload: EventIn,
    service: EventService = Depends(get_event_service),
) -> None:
    service.record(payload)
