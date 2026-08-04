# -*- coding: utf-8 -*-
"""챗봇 대화 유스케이스 — 내 세션 목록/상세(메시지)와 삭제.

멀티테넌시: 모든 접근이 company_id로 격리된다(repository가 강제, IDOR 차단).
세션이 없거나 남의 것이면 repository가 SessionForbiddenError를 던진다(라우터 404).

삭제는 두 종류다 — 대화방 통째(소프트)와 마지막 턴 취소(하드). 정책·경계는
repository 쪽에 주석으로 있다. 여기는 조립만 하고 규칙을 중복 구현하지 않는다.
"""
from app.api.v1.schemas.chat import (
    MessageOut,
    SessionDetailResponse,
    SessionListItem,
    SessionListResponse,
)
from app.infra.db.repositories.chat_repository import ChatRepository

PAGE_SIZE = 20


class ChatQueryService:
    def __init__(self, repository: ChatRepository):
        self._repo = repository

    def list_sessions(self, *, company_id: int, page: int) -> SessionListResponse:
        total = self._repo.count_sessions(company_id)
        offset = (page - 1) * PAGE_SIZE
        rows = self._repo.list_sessions(company_id, limit=PAGE_SIZE, offset=offset)
        items = [
            SessionListItem(
                session_id=str(r.session_id), title=r.title,
                created_at=r.created_at, updated_at=r.updated_at,
            )
            for r in rows
        ]
        return SessionListResponse(
            total=total, page=page, page_size=PAGE_SIZE, items=items)

    def get_session(self, *, session_id: str, company_id: int) -> SessionDetailResponse:
        # 없거나 남의 것이면 SessionForbiddenError(라우터가 404).
        row, msgs = self._repo.get_session_messages(session_id, company_id)
        return SessionDetailResponse(
            session_id=str(row.session_id), title=row.title,
            created_at=row.created_at, updated_at=row.updated_at,
            messages=[MessageOut.model_validate(m) for m in msgs],
        )

    def delete_session(self, *, session_id: str, company_id: int) -> None:
        """대화방 삭제(소프트). 없거나 남의 것이면 SessionForbiddenError."""
        self._repo.soft_delete_session(session_id, company_id)

    def delete_last_turn(self, *, session_id: str, company_id: int) -> int:
        """마지막 턴 취소. 반환 0이면 지울 메시지가 없었다(라우터가 404)."""
        return self._repo.delete_last_turn(session_id, company_id)
