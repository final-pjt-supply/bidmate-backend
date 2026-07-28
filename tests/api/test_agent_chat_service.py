# -*- coding: utf-8 -*-
"""세션 왕복 규약(ADR 0005) + 가드·IDOR·실패경계(ADR-22) — DB 없이 Fake repo로 고정.

왕복: 저장한 session_context가 다음 턴에 '수정 없이 그대로' 들어간다.
가드: 같은 세션 처리 중 재입력은 SessionBusyError.
IDOR: 남의 세션 접근은 SessionForbiddenError.
실패경계: 에이전트가 죽어도 user 메시지는 남는다.
"""
import pytest

from agents.schemas import AgentResponse, Filters, SessionContext

from app.agents import chat_service as cs
from app.agents.chat_service import AgentChatService, SessionBusyError
from app.infra.db.repositories.chat_repository import SessionForbiddenError


def make_ctx(summary: str) -> SessionContext:
    return SessionContext(last_bid_ids=["B1"], last_summary=summary, last_filters=Filters())


class FakeChatRepo:
    """세션ID → (company_id, context) + 메시지 로그. open/close_turn 계약만 구현."""

    def __init__(self):
        self.sessions: dict[str, tuple[int, SessionContext | None]] = {}
        self.messages: list[tuple[str, str, str]] = []
        self._seq = 0

    def new_session_id(self) -> str:
        self._seq += 1
        return f"sid-{self._seq}"

    def open_turn(self, session_id, company_id, user_query):
        if session_id in self.sessions:
            owner, ctx = self.sessions[session_id]
            if owner != company_id:
                raise SessionForbiddenError(session_id)   # IDOR
        else:
            self.sessions[session_id] = (company_id, None)
            ctx = None
        self.messages.append((session_id, "user", user_query))
        return ctx

    def close_turn(self, session_id, resp):
        owner, _ = self.sessions[session_id]
        self.sessions[session_id] = (owner, resp.session_context)
        self.messages.append((session_id, "assistant", resp.answer))


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.responses.pop(0)


def answer(summary: str) -> AgentResponse:
    return AgentResponse(action="answer", answer="답변", session_context=make_ctx(summary))


def test_first_turn_passes_none_context():
    repo, runner = FakeChatRepo(), FakeRunner([answer("턴1")])
    sid, resp = AgentChatService(repo, runner).chat(query="대전 공고", company_id="9001")
    assert runner.requests[0].session_context is None
    assert runner.requests[0].company_id == "9001"          # 에이전트엔 원래 str 그대로
    assert repo.sessions[sid][0] == 9001                    # repo엔 int로 격리
    assert repo.sessions[sid][1] is resp.session_context    # 컨텍스트 저장됨


def test_second_turn_passes_stored_context_verbatim():
    repo, runner = FakeChatRepo(), FakeRunner([answer("턴1"), answer("턴2")])
    svc = AgentChatService(repo, runner)
    sid, _ = svc.chat(query="턴1", company_id="9001")
    turn1_ctx = repo.sessions[sid][1]
    sid2, _ = svc.chat(query="턴2", company_id="9001", session_id=sid)
    assert sid2 == sid
    assert runner.requests[1].session_context is turn1_ctx   # 수정 없이 그대로
    assert repo.sessions[sid][1].last_summary == "턴2"       # 턴2로 갱신


def test_unknown_session_id_is_first_turn_keeping_id():
    repo, runner = FakeChatRepo(), FakeRunner([answer("턴1")])
    sid, _ = AgentChatService(repo, runner).chat(
        query="질문", company_id="9001", session_id="lost")
    assert runner.requests[0].session_context is None
    assert sid == "lost"
    assert "lost" in repo.sessions


def test_entry_bid_id_becomes_entry_context():
    repo, runner = FakeChatRepo(), FakeRunner([answer("턴1")])
    AgentChatService(repo, runner).chat(
        query="이 공고", company_id="9001", entry_bid_id="R26BK_01")
    assert runner.requests[0].entry_context.bid_id == "R26BK_01"


def test_other_company_session_is_forbidden():
    """IDOR — 남의 세션 id로 접근하면 SessionForbiddenError."""
    repo, runner = FakeChatRepo(), FakeRunner([answer("턴1"), answer("x")])
    svc = AgentChatService(repo, runner)
    sid, _ = svc.chat(query="턴1", company_id="9001")
    with pytest.raises(SessionForbiddenError):
        svc.chat(query="침입", company_id="9999", session_id=sid)


def test_user_message_persisted_even_if_agent_fails():
    """실패 경계 — 에이전트가 죽어도 user 메시지는 남고 assistant는 없다."""
    repo = FakeChatRepo()

    def broken(req):
        raise RuntimeError("bedrock down")

    with pytest.raises(RuntimeError):
        AgentChatService(repo, broken).chat(query="질문", company_id="9001", session_id="s1")
    assert ("s1", "user", "질문") in repo.messages
    assert not any(role == "assistant" for _, role, _ in repo.messages)


def test_session_busy_when_turn_in_flight():
    """동시성 — 같은 세션이 이미 처리 중이면 SessionBusyError(→409)."""
    repo, runner = FakeChatRepo(), FakeRunner([answer("x")])
    cs._inflight.add("busy-sid")
    try:
        with pytest.raises(SessionBusyError):
            AgentChatService(repo, runner).chat(
                query="q", company_id="9001", session_id="busy-sid")
    finally:
        cs._inflight.discard("busy-sid")
