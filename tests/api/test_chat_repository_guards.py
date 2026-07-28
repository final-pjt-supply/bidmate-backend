# -*- coding: utf-8 -*-
"""ChatRepository의 삭제 관련 규칙을 DB 없이 고정한다.

계약 테스트(test_chat_sessions_api.py)는 Fake repo라 실 repository 코드를 타지
않는다. 여기서는 진짜 ChatRepository에 Session 스텁을 물려, SQL이 아니라 '판단'
부분만 검증한다:
  - 삭제된 세션은 되살아나지 않는다(open_turn 가드)
  - 마지막 턴은 assistant + 직전 user 한 쌍, 답변 없는 턴은 한 건만
  - 턴을 지우면 session_context를 비운다(되돌릴 수 없으므로)

실제 SQL(ORDER BY DESC LIMIT 2, UPDATE) 동등성은 로컬 Postgres 통합 테스트 몫이다
— 운영 RDS에는 쓰기 테스트를 하지 않는다(test_bid_repository_integration.py 참고).
"""
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.infra.db.repositories.chat_repository import (
    ChatRepository,
    SessionForbiddenError,
)

CID = 9001
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_NOW = datetime(2026, 7, 28, 10, 0)


def _session_row(*, company_id=CID, deleted_at=None, context={"last_summary": "x"}):
    return SimpleNamespace(session_id=SID, company_id=company_id, title="대전 공고",
                           session_context=context, created_at=_NOW, updated_at=_NOW,
                           deleted_at=deleted_at)


def _msg(role, content="..."):
    return SimpleNamespace(role=role, content=content, created_at=_NOW)


class StubSession:
    """SQLAlchemy Session 최소 대역 — get/execute/delete/add/commit만."""

    def __init__(self, row=None, tail=()):
        self._row = row
        self._tail = list(tail)      # execute()가 돌려줄 '최근순' 메시지
        self.deleted = []
        self.added = []
        self.commits = 0

    def get(self, _model, _pk):
        return self._row

    def execute(self, _stmt):
        rows = self._tail
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    def delete(self, obj):
        self.deleted.append(obj)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        self.commits += 1


# ── 삭제된 세션 부활 방지 ────────────────────────────────────
def test_open_turn_rejects_deleted_session():
    """지운 session_id로 다시 질문해도 그 세션에 메시지가 쌓이면 안 된다."""
    s = StubSession(row=_session_row(deleted_at=_NOW))
    with pytest.raises(SessionForbiddenError):
        ChatRepository(s).open_turn(SID, CID, "다시 물어봄")
    assert s.added == [] and s.commits == 0


def test_open_turn_rejects_other_company_session():
    s = StubSession(row=_session_row(company_id=12345))
    with pytest.raises(SessionForbiddenError):
        ChatRepository(s).open_turn(SID, CID, "남의 세션")
    assert s.added == []


# ── 대화방 삭제(소프트) ──────────────────────────────────────
def test_soft_delete_stamps_deleted_at():
    row = _session_row()
    s = StubSession(row=row)
    ChatRepository(s).soft_delete_session(SID, CID)
    assert row.deleted_at is not None and s.commits == 1
    assert s.deleted == []          # 행을 지우지 않는다


@pytest.mark.parametrize("row", [None, _session_row(company_id=12345),
                                 _session_row(deleted_at=_NOW)])
def test_soft_delete_rejects_missing_other_and_already_deleted(row):
    with pytest.raises(SessionForbiddenError):
        ChatRepository(StubSession(row=row)).soft_delete_session(SID, CID)


# ── 마지막 턴 취소(하드) ─────────────────────────────────────
def test_last_turn_deletes_assistant_and_preceding_user():
    row = _session_row()
    tail = [_msg("assistant", "답변"), _msg("user", "질문")]   # 최근순
    s = StubSession(row=row, tail=tail)
    assert ChatRepository(s).delete_last_turn(SID, CID) == 2
    assert [m.role for m in s.deleted] == ["assistant", "user"]
    assert row.session_context is None      # 되돌릴 수 없으므로 문맥을 비운다
    assert s.commits == 1


def test_last_turn_deletes_lone_user_message():
    """에이전트가 죽어 답변이 없는 턴 — 질문 한 건만 지운다."""
    row = _session_row()
    s = StubSession(row=row, tail=[_msg("user", "답 못 받은 질문"),
                                   _msg("assistant", "이전 턴 답변")])
    assert ChatRepository(s).delete_last_turn(SID, CID) == 1
    assert [m.content for m in s.deleted] == ["답 못 받은 질문"]


def test_last_turn_on_empty_session_deletes_nothing():
    row = _session_row()
    s = StubSession(row=row, tail=[])
    assert ChatRepository(s).delete_last_turn(SID, CID) == 0
    assert s.deleted == [] and s.commits == 0
    assert row.session_context is not None   # 손대지 않는다


def test_last_turn_rejects_deleted_session():
    s = StubSession(row=_session_row(deleted_at=_NOW), tail=[_msg("assistant")])
    with pytest.raises(SessionForbiddenError):
        ChatRepository(s).delete_last_turn(SID, CID)
    assert s.deleted == []
