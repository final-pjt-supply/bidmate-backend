# -*- coding: utf-8 -*-
"""POST /agent/chat 계약 테스트 — HTTP 매핑·세션ID 왕복·상태코드(401/409/502).

company_id는 토큰(get_authenticated_user)에서만 온다 — 요청 body엔 없다(멀티테넌시).
왕복/가드/IDOR 자체는 test_agent_chat_service.py, 에이전트 로직은 agents 패키지.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.schemas import AgentResponse, Citation, Filters, SessionContext

from app.agents import chat_service as cs
from app.agents.chat_rate_limit import guard as chat_guard
from app.agents.chat_service import AgentChatService
from app.api.deps import (
    get_agent_chat_service,
    get_authenticated_user,
    get_chat_usage_repo,
)
from app.main import app

_COMPANY = "9001"


class FakeUsageRepo:
    """일일 카운터 대역 — 실제 RDS를 안 건드린다. 호출마다 +1."""

    def __init__(self):
        self.count = 0

    def incr_and_get(self, company_id):
        self.count += 1
        return self.count


def make_ctx(summary: str = "턴1") -> SessionContext:
    return SessionContext(last_bid_ids=["B1"], last_summary=summary, last_filters=Filters())


class FakeChatRepo:
    def __init__(self):
        self.sessions = {}
        self._seq = 0

    def new_session_id(self):
        self._seq += 1
        return f"sid-{self._seq}"

    def open_turn(self, session_id, company_id, user_query):
        if session_id in self.sessions:
            owner, ctx = self.sessions[session_id]
        else:
            self.sessions[session_id] = (company_id, None)
            ctx = None
        return ctx

    def count_turns(self, session_id, company_id):
        return 0            # 캡 미도달 — API 계약 테스트는 소프트캡을 다루지 않는다

    def close_turn(self, session_id, resp):
        owner, _ = self.sessions[session_id]
        self.sessions[session_id] = (owner, resp.session_context)


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req):
        self.requests.append(req)
        return self.responses.pop(0)


@pytest.fixture
def client_with_runner():
    def _make(responses) -> tuple[TestClient, FakeRunner]:
        chat_guard.reset()          # 프로세스 전역 레이트 가드 초기화(테스트 격리)
        runner = FakeRunner(responses)
        # 서비스는 '한 번만' 만든다 — 요청마다 새로 만들면 FakeChatRepo가 리셋돼
        # 세션 컨텍스트 왕복이 안 이어진다.
        service = AgentChatService(FakeChatRepo(), runner)
        app.dependency_overrides[get_agent_chat_service] = lambda: service
        app.dependency_overrides[get_chat_usage_repo] = lambda: FakeUsageRepo()
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id=_COMPANY, email=None)
        return TestClient(app), runner

    yield _make
    app.dependency_overrides.clear()
    chat_guard.reset()


def test_answer_turn_returns_session_id_and_maps_fields(client_with_runner):
    citation = Citation(bid_id="B1", file_id="f1", chunk_idx=0, text="원문")
    client, runner = client_with_runner([
        AgentResponse(action="answer", answer="대전 공고 2건입니다.",
                      citations=[citation], session_context=make_ctx())])
    r = client.post("/agent/chat", json={"query": "대전 공고 알려줘", "entry_bid_id": "R26BK_01"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"]
    assert body["action"] == "answer"
    assert body["answer"] == "대전 공고 2건입니다."
    assert body["citations"][0]["bid_id"] == "B1"
    assert "session_context" not in body
    # company_id는 토큰에서 온다(요청 body가 아님)
    assert runner.requests[0].company_id == _COMPANY
    assert runner.requests[0].entry_context.bid_id == "R26BK_01"


def test_second_turn_reuses_session(client_with_runner):
    client, runner = client_with_runner([
        AgentResponse(action="answer", answer="답1", session_context=make_ctx("턴1")),
        AgentResponse(action="answer", answer="답2", session_context=make_ctx("턴2"))])
    sid = client.post("/agent/chat", json={"query": "턴1"}).json()["session_id"]
    r2 = client.post("/agent/chat", json={"query": "턴2", "session_id": sid})
    assert r2.json()["session_id"] == sid
    assert runner.requests[1].session_context.last_summary == "턴1"


def test_clarify_turn_maps_clarify_message(client_with_runner):
    client, _ = client_with_runner([
        AgentResponse(action="clarify", clarify_message="지역을 알려주세요.",
                      session_context=make_ctx())])
    body = client.post("/agent/chat", json={"query": "공고 찾아줘"}).json()
    assert body["action"] == "clarify"
    assert body["clarify_message"] == "지역을 알려주세요."
    assert body["answer"] is None


def test_redirect_turn_maps_filters(client_with_runner):
    client, _ = client_with_runner([
        AgentResponse(action="redirect", redirect_filters=Filters(region="대전"),
                      session_context=make_ctx())])
    body = client.post("/agent/chat", json={"query": "대전 공고 목록 보여줘"}).json()
    assert body["action"] == "redirect"
    assert body["redirect_filters"]["region"] == "대전"


def test_query_is_required(client_with_runner):
    client, _ = client_with_runner([])
    assert client.post("/agent/chat", json={}).status_code == 422


def test_empty_query_is_422(client_with_runner):
    client, runner = client_with_runner([])
    assert client.post("/agent/chat", json={"query": ""}).status_code == 422
    assert runner.requests == []           # LLM까지 가지 않는다(비용 방어)


def test_whitespace_only_query_is_422(client_with_runner):
    """공백만("   ")도 422 — trim 후 비어 있으면 LLM에 안 보낸다(비용 방어)."""
    client, runner = client_with_runner([])
    assert client.post("/agent/chat", json={"query": "   "}).status_code == 422
    assert runner.requests == []


def test_too_long_query_is_422(client_with_runner):
    client, runner = client_with_runner([])
    assert client.post("/agent/chat", json={"query": "가" * 501}).status_code == 422
    assert runner.requests == []


def test_chat_requires_login(client_with_runner):
    """★ 인증 필수 — 토큰 없으면 401(company_id를 클라가 못 정한다)."""
    client_with_runner([AgentResponse(action="answer", answer="x", session_context=make_ctx())])
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).post("/agent/chat", json={"query": "질문"}).status_code == 401


def test_agent_failure_maps_to_502(client_with_runner):
    def broken_runner(req):
        raise RuntimeError("Bedrock down")

    client, _ = client_with_runner([])
    app.dependency_overrides[get_agent_chat_service] = lambda: AgentChatService(
        FakeChatRepo(), broken_runner)
    r = client.post("/agent/chat", json={"query": "질문"})
    assert r.status_code == 502
    assert "다시 시도" in r.json()["detail"]


def test_session_busy_maps_to_409(client_with_runner):
    client, _ = client_with_runner([
        AgentResponse(action="answer", answer="x", session_context=make_ctx())])
    cs._inflight.add("busy-1")
    try:
        r = client.post("/agent/chat", json={"query": "q", "session_id": "busy-1"})
        assert r.status_code == 409
    finally:
        cs._inflight.discard("busy-1")


def test_minute_rate_limit_maps_to_429(client_with_runner):
    """★ 회사당 분당 상한(기본 10) 초과 → 429 + Retry-After(비용·부하 방어)."""
    client, _ = client_with_runner([
        AgentResponse(action="answer", answer="x", session_context=make_ctx())
        for _ in range(12)])
    for _ in range(10):                                    # 10회는 통과
        assert client.post("/agent/chat", json={"query": "q"}).status_code == 200
    r = client.post("/agent/chat", json={"query": "q"})    # 11회째 차단
    assert r.status_code == 429
    assert r.headers.get("Retry-After")


def test_daily_rate_limit_maps_to_429(client_with_runner):
    """★ 일일 상한(기본 500) 초과 → 429. usage가 상한 초과 값을 반환하는 상황."""
    client, _ = client_with_runner([
        AgentResponse(action="answer", answer="x", session_context=make_ctx())])
    app.dependency_overrides[get_chat_usage_repo] = lambda: SimpleNamespace(
        incr_and_get=lambda cid: 99999)                    # 하루 상한 초과 상태
    r = client.post("/agent/chat", json={"query": "q"})
    assert r.status_code == 429
    assert "오늘" in r.json()["detail"]
