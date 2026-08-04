# -*- coding: utf-8 -*-
"""에이전트 대화 서비스 — 세션 왕복 규약(ADR 0005)의 소유자.

규약: 이전 턴 AgentResponse.session_context를 저장했다가 다음 턴
AgentRequest.session_context에 '수정 없이 그대로' 넣는다. 모르는(만료·유실된)
session_id는 첫 턴으로 간주한다 — 서버 재시작(인메모리 소실) 후에도 프론트가
깨지지 않게 하기 위함이다.

runner는 주입받는다. 운영 runner는 AgentServiceClient(HTTP, POST /turn) —
에이전트가 별도 프로세스로 분리되면서 in-process `run_agent` 직접 호출을 대체했다
(app/agents/agent_client.py). 백엔드는 이제 에이전트 구현이 아니라 계약
(`agents.schemas`)만 임포트하므로, 여기서 하는 일은 순수한 세션 왕복뿐이다.
테스트는 가짜 runner를 넣어 에이전트 프로세스 없이 왕복 규약만 검증한다.
"""
from typing import Callable

from agents.schemas import AgentRequest, AgentResponse, EntryContext

from app.agents.session_store import InMemorySessionStore

# 한 턴을 처리하는 호출 가능 객체. 운영은 HTTP 클라이언트, 테스트는 가짜.
Runner = Callable[[AgentRequest], AgentResponse]


class AgentChatService:
    def __init__(self, store: InMemorySessionStore, runner: Runner):
        self._store = store
        self._runner = runner

    def chat(self, *, query: str, company_id: str,
             entry_bid_id: str | None = None,
             session_id: str | None = None) -> tuple[str, AgentResponse]:
        ctx = self._store.get(session_id) if session_id else None
        req = AgentRequest(query=query, company_id=company_id,
                           entry_context=EntryContext(bid_id=entry_bid_id),
                           session_context=ctx)
        resp = self._runner(req)
        sid = session_id or self._store.new_session_id()
        self._store.set(sid, resp.session_context)
        return sid, resp
