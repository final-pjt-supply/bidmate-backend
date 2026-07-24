# -*- coding: utf-8 -*-
"""에이전트 대화 서비스 — 세션 왕복 규약(ADR 0005)의 소유자.

규약: 이전 턴 AgentResponse.session_context를 저장했다가 다음 턴
AgentRequest.session_context에 '수정 없이 그대로' 넣는다. 모르는(만료·유실된)
session_id는 첫 턴으로 간주한다 — 서버 재시작(인메모리 소실) 후에도 프론트가
깨지지 않게 하기 위함이다.

기본 runner는 agents.run.run_agent(같은 프로세스 직접 호출, ADR 0005).
run_agent는 Bedrock을 부르므로(agents/llm.py) 배포 env에 AWS_ACCESS_KEY/
AWS_SECRET_KEY가 필요하다. 테스트는 가짜 runner를 주입해 Bedrock 없이
왕복 규약만 검증한다.
"""
from typing import Callable

from agents.run import run_agent
from agents.schemas import AgentRequest, AgentResponse, EntryContext

from app.agents.session_store import InMemorySessionStore


class AgentChatService:
    def __init__(self, store: InMemorySessionStore,
                 runner: Callable[[AgentRequest], AgentResponse] = run_agent):
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
