# -*- coding: utf-8 -*-
"""세션 왕복 규약(ADR 0005) 테스트 — 저장한 session_context가 다음 턴 요청에
'수정 없이 그대로' 들어가는지를 가짜 runner로 고정한다(Bedrock 불필요)."""
from agents.schemas import AgentResponse, Filters, SessionContext

from app.agents.session_store import InMemorySessionStore
from app.services.agent_chat_service import AgentChatService


def make_ctx(summary: str) -> SessionContext:
    return SessionContext(last_bid_ids=["B1"], last_summary=summary,
                          last_filters=Filters())


class FakeRunner:
    """받은 AgentRequest를 기록하고 미리 정한 응답을 돌려준다."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.responses.pop(0)


def answer(summary: str) -> AgentResponse:
    return AgentResponse(action="answer", answer="답변",
                         session_context=make_ctx(summary))


def test_first_turn_creates_session_and_passes_none_context():
    store = InMemorySessionStore()
    runner = FakeRunner([answer("턴1")])
    sid, resp = AgentChatService(store, runner).chat(
        query="대전 공고 알려줘", company_id="C1")
    assert runner.requests[0].session_context is None
    assert runner.requests[0].query == "대전 공고 알려줘"
    assert runner.requests[0].company_id == "C1"
    assert store.get(sid) is resp.session_context      # 응답 컨텍스트가 저장됨


def test_second_turn_passes_stored_context_verbatim():
    store = InMemorySessionStore()
    runner = FakeRunner([answer("턴1"), answer("턴2")])
    service = AgentChatService(store, runner)
    sid, _ = service.chat(query="턴1 질문", company_id="C1")
    turn1_ctx = store.get(sid)
    sid2, _ = service.chat(query="턴2 질문", company_id="C1", session_id=sid)
    assert sid2 == sid
    assert runner.requests[1].session_context is turn1_ctx   # 수정 없이 그대로
    assert store.get(sid).last_summary == "턴2"              # 턴2 컨텍스트로 갱신


def test_unknown_session_id_is_treated_as_first_turn():
    """서버 재시작(인메모리 소실) 후에도 프론트가 깨지지 않게 — 모르는 세션은
    첫 턴으로 간주하되 클라이언트의 session_id는 유지한다."""
    store = InMemorySessionStore()
    runner = FakeRunner([answer("턴1")])
    sid, _ = AgentChatService(store, runner).chat(
        query="질문", company_id="C1", session_id="잃어버린-세션")
    assert runner.requests[0].session_context is None
    assert sid == "잃어버린-세션"
    assert store.get("잃어버린-세션") is not None


def test_entry_bid_id_becomes_entry_context():
    store = InMemorySessionStore()
    runner = FakeRunner([answer("턴1")])
    AgentChatService(store, runner).chat(
        query="이 공고 자격 되나요", company_id="C1", entry_bid_id="R26BK_01")
    assert runner.requests[0].entry_context.bid_id == "R26BK_01"
