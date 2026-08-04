# -*- coding: utf-8 -*-
"""AgentServiceClient 계약 테스트 — 백엔드가 에이전트 서비스를 부르는 실제 트래픽
경로(POST /turn)를 고정한다. httpx.MockTransport로 에이전트 프로세스 없이 검증한다.

여기서 지키는 것: 요청 URL/본문(계약 그대로 직렬화), 응답 파싱, 실패 시 예외 전파
(라우터가 502로 바꾸므로 삼키면 안 된다).
"""
import json

import httpx
import pytest
from pydantic import ValidationError

from agents.schemas import (
    AgentRequest,
    AgentResponse,
    EntryContext,
    Filters,
    Region,
    SessionContext,
)

from app.agents.agent_client import AgentServiceClient

_BASE_URL = "http://127.0.0.1:8010"


def make_ctx(summary: str = "턴1") -> SessionContext:
    return SessionContext(last_bid_ids=["B1"], last_summary=summary,
                          last_filters=Filters(region=Region.DAEJEON))


def ok_response(**overrides) -> dict:
    body = AgentResponse(action="answer", answer="대전 공고 2건입니다.",
                         session_context=make_ctx()).model_dump(mode="json")
    body.update(overrides)
    return body


def client_for(handler) -> tuple[AgentServiceClient, list[httpx.Request]]:
    """handler가 만든 응답을 돌려주는 클라이언트 + 실제로 나간 요청 기록."""
    seen: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(_capture)
    return (
        AgentServiceClient(httpx.Client(base_url=_BASE_URL, transport=transport)),
        seen,
    )


def request(session_context: SessionContext | None = None) -> AgentRequest:
    return AgentRequest(query="대전 공고 알려줘", company_id="C1",
                        entry_context=EntryContext(bid_id="R26BK_01"),
                        session_context=session_context)


def test_posts_to_turn_with_contract_body():
    client, seen = client_for(lambda r: httpx.Response(200, json=ok_response()))
    client(request())
    assert str(seen[0].url) == f"{_BASE_URL}/turn"
    assert seen[0].method == "POST"
    body = json.loads(seen[0].content)
    assert body["query"] == "대전 공고 알려줘"
    assert body["company_id"] == "C1"
    assert body["entry_context"]["bid_id"] == "R26BK_01"
    assert body["session_context"] is None          # 첫 턴


def test_session_context_is_sent_verbatim():
    """왕복 규약의 마지막 구간 — 저장해둔 컨텍스트가 손실 없이 에이전트까지 간다.
    StrEnum(Region)이 JSON 원시값으로 나가는지도 같이 고정한다."""
    client, seen = client_for(lambda r: httpx.Response(200, json=ok_response()))
    ctx = make_ctx("직전 턴 요약")
    client(request(ctx))
    sent = json.loads(seen[0].content)["session_context"]
    assert sent == ctx.model_dump(mode="json")
    assert sent["last_filters"]["region"] == "대전"   # 문자열로 직렬화


def test_parses_agent_response():
    client, _ = client_for(lambda r: httpx.Response(200, json=ok_response()))
    resp = client(request())
    assert isinstance(resp, AgentResponse)
    assert resp.action == "answer"
    assert resp.answer == "대전 공고 2건입니다."
    assert resp.session_context.last_summary == "턴1"


@pytest.mark.parametrize("status", [422, 500, 502, 504])
def test_error_status_raises(status):
    """비2xx는 삼키지 않는다 — 라우터가 502 + 재시도 안내로 바꿔야 하기 때문."""
    client, _ = client_for(lambda r: httpx.Response(status, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        client(request())


def test_connect_error_raises():
    """에이전트 프로세스가 안 떠 있을 때 — 조용히 None을 돌려 세션을 오염시키면 안 된다."""
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, _ = client_for(_refuse)
    with pytest.raises(httpx.ConnectError):
        client(request())


def test_contract_violation_raises():
    """session_context 없는 응답은 계약 위반 — 반쯤 빈 응답을 세션에 저장하지 않는다."""
    client, _ = client_for(
        lambda r: httpx.Response(200, json={"action": "answer", "answer": "x"}))
    with pytest.raises(ValidationError):
        client(request())
