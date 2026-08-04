# -*- coding: utf-8 -*-
"""챗봇 대화 DB 접근 — 세션/메시지/컨텍스트 (ADR-22).

인메모리 세션(ADR 0005)을 RDS로 영속화. session_context(에이전트 왕복, 불투명)는
JSONB로 직렬화 저장한다. 모든 접근은 company_id로 격리(멀티테넌시·IDOR 방지).

실패 경계: open_turn이 user 메시지를 먼저 커밋한다 — 에이전트가 실패해도 질문은
남아 재시도할 수 있다. close_turn은 성공 시에만 assistant 메시지 + 컨텍스트를 커밋.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
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
            # 삭제한 세션은 되살리지 않는다 — 클라가 지운 session_id를 그대로 들고
            # 다음 질문을 보내면 목록에 안 보이는 세션에 메시지만 쌓인다.
            if row.company_id != company_id or row.deleted_at is not None:
                raise SessionForbiddenError(str(session_id))   # IDOR / 삭제됨
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

    def count_turns(self, session_id, company_id: int) -> int:
        """세션의 user 메시지 수(=턴 수). 소프트캡 판정용.

        모르는 session_id(아직 없는 새 대화)는 0 — 캡에 안 걸린다. 다른 회사
        소유거나 삭제된 세션이면 IDOR로 막는다(open_turn과 같은 신뢰 기준).
        """
        row = self._s.get(ChatSession, _to_uuid(session_id))
        if row is None:
            return 0
        if row.company_id != company_id or row.deleted_at is not None:
            raise SessionForbiddenError(str(session_id))
        return self._s.execute(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.session_id == row.session_id,
                   ChatMessage.role == "user")
        ).scalar_one()

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

    # ── 삭제 ──────────────────────────────────────────────────
    def soft_delete_session(self, session_id, company_id: int) -> None:
        """대화방 삭제 — deleted_at에 시각만 기록(행은 남긴다).

        조회(list/count/get)가 이미 deleted_at IS NULL로 거르므로 화면에서 사라진다.
        실삭제는 회사 탈퇴 시 FK CASCADE가 맡는다(개보법 삭제권).
        없음/남의 것/이미 삭제됨은 모두 SessionForbiddenError → 라우터 404(존재 은닉).
        """
        sid = _to_uuid(session_id)
        row = self._s.get(ChatSession, sid)
        if row is None or row.company_id != company_id or row.deleted_at is not None:
            raise SessionForbiddenError(str(session_id))
        row.deleted_at = _now()
        self._s.commit()

    def delete_last_turn(self, session_id, company_id: int) -> int:
        """마지막 턴 취소 — 끝 메시지 한 쌍(assistant + 직전 user)을 실제로 지운다.

        '되돌리기' 성격이라 하드 삭제한다(chat_messages엔 소프트 삭제 칸이 없다).
        session_context는 NULL로 비운다 — 직전 컨텍스트를 보관하지 않아 되돌릴 수
        없기 때문. 지운 말을 봇이 계속 기억하는 것보다 문맥 초기화가 낫다.
        에이전트가 실패해 user 메시지만 남은 턴은 그 한 건만 지운다.
        반환: 지운 메시지 수(0 = 지울 게 없음 → 라우터 404).
        """
        sid = _to_uuid(session_id)
        row = self._s.get(ChatSession, sid)
        if row is None or row.company_id != company_id or row.deleted_at is not None:
            raise SessionForbiddenError(str(session_id))

        stmt = (select(ChatMessage).where(ChatMessage.session_id == sid)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(2))
        tail = list(self._s.execute(stmt).scalars().all())
        if not tail:
            return 0
        victims = [tail[0]]
        if tail[0].role == "assistant" and len(tail) > 1 and tail[1].role == "user":
            victims.append(tail[1])
        for m in victims:
            self._s.delete(m)
        row.session_context = None
        row.updated_at = _now()
        self._s.commit()
        return len(victims)

    @staticmethod
    def _title(q: str) -> str | None:
        q = (q or "").strip()
        return q[:30] or None
