# -*- coding: utf-8 -*-
"""POST /agent/chat 계약 테스트 — HTTP 매핑과 세션ID 왕복만 본다(왕복 규약
자체는 test_agent_chat_service.py가, 에이전트 로직은 agents 패키지가 담당)."""
import pytest
from fastapi.testclient import TestClient

from agents.schemas import AgentResponse, Citation, Filters, SessionContext

from app.agents.chat_service import AgentChatService
from app.agents.session_store import InMemorySessionStore
from app.api.deps import get_agent_chat_service
from app.main import app


def make_ctx(summary: str = "턴1") -> SessionContext:
    return SessionContext(last_bid_ids=["B1"], last_summary=summary,
                          last_filters=Filters())


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.responses.pop(0)


@pytest.fixture
def client_with_runner():
    """미리 정한 AgentResponse 목록으로 뜬 TestClient 팩토리."""
    def _make(responses) -> tuple[TestClient, FakeRunner]:
        runner = FakeRunner(responses)
        service = AgentChatService(InMemorySessionStore(), runner)
        app.dependency_overrides[get_agent_chat_service] = lambda: service
        return TestClient(app), runner

    yield _make
    app.dependency_overrides.clear()


def test_answer_turn_returns_session_id_and_maps_fields(client_with_runner):
    citation = Citation(bid_id="B1", file_id="f1", chunk_idx=0, text="원문")
    client, runner = client_with_runner([
        AgentResponse(action="answer", answer="대전 공고 2건입니다.",
                      citations=[citation], session_context=make_ctx())])
    r = client.post("/agent/chat", json={
        "query": "대전 공고 알려줘", "company_id": "C1",
        "entry_bid_id": "R26BK_01"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"]                       # 서버가 발급
    assert body["action"] == "answer"
    assert body["answer"] == "대전 공고 2건입니다."
    assert body["citations"][0]["bid_id"] == "B1"
    assert "session_context" not in body            # 컨텍스트는 서버 보관
    assert runner.requests[0].entry_context.bid_id == "R26BK_01"


def test_second_turn_reuses_session(client_with_runner):
    client, runner = client_with_runner([
        AgentResponse(action="answer", answer="답1",
                      session_context=make_ctx("턴1")),
        AgentResponse(action="answer", answer="답2",
                      session_context=make_ctx("턴2"))])
    sid = client.post("/agent/chat", json={
        "query": "턴1", "company_id": "C1"}).json()["session_id"]
    r2 = client.post("/agent/chat", json={
        "query": "턴2", "company_id": "C1", "session_id": sid})
    assert r2.json()["session_id"] == sid
    assert runner.requests[1].session_context.last_summary == "턴1"


def test_clarify_turn_maps_clarify_message(client_with_runner):
    client, _ = client_with_runner([
        AgentResponse(action="clarify", clarify_message="지역을 알려주세요.",
                      session_context=make_ctx())])
    body = client.post("/agent/chat", json={
        "query": "공고 찾아줘", "company_id": "C1"}).json()
    assert body["action"] == "clarify"
    assert body["clarify_message"] == "지역을 알려주세요."
    assert body["answer"] is None


def test_redirect_turn_maps_filters(client_with_runner):
    client, _ = client_with_runner([
        AgentResponse(action="redirect",
                      redirect_filters=Filters(region="대전"),
                      session_context=make_ctx())])
    body = client.post("/agent/chat", json={
        "query": "대전 공고 목록 보여줘", "company_id": "C1"}).json()
    assert body["action"] == "redirect"
    assert body["redirect_filters"]["region"] == "대전"


def test_query_is_required(client_with_runner):
    client, _ = client_with_runner([])
    r = client.post("/agent/chat", json={"company_id": "C1"})
    assert r.status_code == 422


def test_empty_query_is_422(client_with_runner):
    client, runner = client_with_runner([])
    r = client.post("/agent/chat", json={"query": "", "company_id": "C1"})
    assert r.status_code == 422
    assert runner.requests == []           # LLM까지 가지 않는다(비용 방어)


def test_too_long_query_is_422(client_with_runner):
    client, runner = client_with_runner([])
    r = client.post("/agent/chat", json={"query": "가" * 501, "company_id": "C1"})
    assert r.status_code == 422
    assert runner.requests == []


def test_agent_failure_maps_to_502(client_with_runner):
    """Bedrock 장애 등 에이전트 실패를 맨몸 500 대신 502+메시지로 내린다 —
    프론트가 '일시 오류, 재시도' UI를 만들 수 있는 규약."""
    def broken_runner(req):
        raise RuntimeError("Bedrock down")

    client, _ = client_with_runner([])
    service = AgentChatService(InMemorySessionStore(), broken_runner)
    app.dependency_overrides[get_agent_chat_service] = lambda: service
    r = client.post("/agent/chat", json={"query": "질문", "company_id": "C1"})
    assert r.status_code == 502
    assert "다시 시도" in r.json()["detail"]
