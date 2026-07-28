# -*- coding: utf-8 -*-
"""챗봇 대화 DB 접근 — 세션/메시지/컨텍스트 (ADR-22).

인메모리 세션(ADR 0005)을 RDS로 영속화. session_context(에이전트 왕복, 불투명)는
JSONB로 직렬화 저장한다. 모든 접근은 company_id로 격리(멀티테넌시·IDOR 방지).

실패 경계: open_turn이 user 메시지를 먼저 커밋한다 — 에이전트가 실패해도 질문은
남아 재시도할 수 있다. close_turn은 성공 시에만 assistant 메시지 + 컨텍스트를 커밋.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.schemas import AgentResponse, SessionContext
from app.infra.db.models.chat import ChatMessage, ChatSession

_KST = timezone(timedelta(hours=9))


def _now() -> datetime:
    return datetime.now(_KST).replace(tzinfo=None)


def _to_uuid(sid) -> uuid.UUID:
    return sid if isinstance(sid, uuid.UUID) else uuid.UUID(str(sid))


class SessionForbiddenError(Exception):
    """없거나 다른 회사 소유인 세션 접근(IDOR). 라우터가 404로 통일(존재 은닉)."""


class ChatRepository:
    def __init__(self, session: Session):
        self._s = session

    def new_session_id(self) -> str:
        return str(uuid.uuid4())

    # ── 대화 턴 ────────────────────────────────────────────────
    def open_turn(self, session_id, company_id: int, user_query: str) -> SessionContext | None:
        """턴 시작 — 세션 확보(신규 생성 or 소유 검증) + user 메시지 커밋.

        기존 세션인데 다른 회사 것이면 IDOR로 막는다. 모르는 session_id(클라가
        임의로 준)는 그 id로 새 세션을 만든다(ADR 0005: 첫 턴 취급, id는 유지).
        반환: 기존 세션의 저장된 컨텍스트(다음 턴 되먹임) or None.
        """
        sid = _to_uuid(session_id)
        row = self._s.get(ChatSession, sid)
        if row is not None:
            if row.company_id != company_id:
                raise SessionForbiddenError(str(session_id))   # IDOR
            ctx = (SessionContext.model_validate(row.session_context)
                   if row.session_context else None)
        else:
            self._s.add(ChatSession(
                session_id=sid, company_id=company_id,
                title=self._title(user_query), created_at=_now(), updated_at=_now(),
            ))
            # 메시지 FK가 세션을 참조하므로 세션을 먼저 INSERT한다(relationship 없이
            # 섞으면 UOW가 순서를 못 잡아 FK 위반). flush로 세션 행을 먼저 확정.
            self._s.flush()
            ctx = None
        # 실패 경계: user 메시지 먼저 저장하고 커밋한다.
        self._s.add(ChatMessage(
            session_id=sid, role="user", content=user_query, created_at=_now()))
        self._s.commit()
        return ctx

    def close_turn(self, session_id, resp: AgentResponse) -> None:
        """턴 성공 — assistant 메시지 + session_context 갱신 커밋."""
        sid = _to_uuid(session_id)
        content = resp.answer or resp.clarify_message
        meta = {
            "action": resp.action,
            "citations": [c.model_dump(mode="json") for c in (resp.citations or [])],
            "redirect_filters": (resp.redirect_filters.model_dump(mode="json")
                                 if resp.redirect_filters else None),
        }
        self._s.add(ChatMessage(
            session_id=sid, role="assistant", content=content,
            response_meta=meta, created_at=_now()))
        row = self._s.get(ChatSession, sid)
        if row is not None:
            row.session_context = (resp.session_context.model_dump(mode="json")
                                   if resp.session_context else None)
            row.updated_at = _now()
        self._s.commit()

    # ── 조회 ──────────────────────────────────────────────────
    def list_sessions(self, company_id: int, *, limit: int, offset: int) -> list[ChatSession]:
        stmt = (
            select(ChatSession)
            .where(ChatSession.company_id == company_id, ChatSession.deleted_at.is_(None))
            .order_by(ChatSession.updated_at.desc())
            .limit(limit).offset(offset)
        )
        return list(self._s.execute(stmt).scalars().all())

    def count_sessions(self, company_id: int) -> int:
        from sqlalchemy import func
        stmt = (select(func.count()).select_from(ChatSession)
                .where(ChatSession.company_id == company_id,
                       ChatSession.deleted_at.is_(None)))
        return self._s.execute(stmt).scalar_one()

    def get_session_messages(self, session_id, company_id: int) -> tuple[ChatSession, list[ChatMessage]]:
        """세션 + 메시지(시간순). 없거나 남의 것이면 SessionForbiddenError(404)."""
        sid = _to_uuid(session_id)
        row = self._s.get(ChatSession, sid)
        if row is None or row.company_id != company_id or row.deleted_at is not None:
            raise SessionForbiddenError(str(session_id))
        stmt = (select(ChatMessage).where(ChatMessage.session_id == sid)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc()))
        return row, list(self._s.execute(stmt).scalars().all())

    @staticmethod
    def _title(q: str) -> str | None:
        q = (q or "").strip()
        return q[:30] or None
