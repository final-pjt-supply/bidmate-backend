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

run_agent는 지연 임포트한다 — agents.llm이 임포트 시점에 load_dotenv()로
자기 리포(editable 설치 시 Final_Main_Agent)의 .env를 os.environ에 주입하는
부작용이 있어, 백엔드 Settings(app.config)가 먼저 로드되기 전에 agents가
임포트되면 그쪽 POSTGRES_* 값이 백엔드 DB 설정을 오염시킨다.
"""
from typing import Callable

from agents.schemas import AgentRequest, AgentResponse, EntryContext

from app.agents.session_store import InMemorySessionStore


class AgentChatService:
    def __init__(self, store: InMemorySessionStore,
                 runner: Callable[[AgentRequest], AgentResponse] | None = None):
        self._store = store
        self._runner = runner          # None이면 첫 호출에 run_agent를 늦게 묶는다

    def _run(self, req: AgentRequest) -> AgentResponse:
        if self._runner is None:
            from agents.run import run_agent
            self._runner = run_agent
        return self._runner(req)

    def chat(self, *, query: str, company_id: str,
             entry_bid_id: str | None = None,
             session_id: str | None = None) -> tuple[str, AgentResponse]:
        ctx = self._store.get(session_id) if session_id else None
        req = AgentRequest(query=query, company_id=company_id,
                           entry_context=EntryContext(bid_id=entry_bid_id),
                           session_context=ctx)
        resp = self._run(req)
        sid = session_id or self._store.new_session_id()
        self._store.set(sid, resp.session_context)
        return sid, resp
