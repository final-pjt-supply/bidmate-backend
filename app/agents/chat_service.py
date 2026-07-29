# -*- coding: utf-8 -*-
"""에이전트 대화 서비스 — 세션 왕복 규약(ADR 0005) + DB 영속화(ADR-22).

규약: 이전 턴 AgentResponse.session_context를 저장했다가 다음 턴 AgentRequest에
'수정 없이 그대로' 넣는다. 이제 인메모리가 아니라 RDS(ChatRepository)에 보관한다
— 서버 재시작·멀티턴에도 문맥이 유지되고 대화 이력이 남는다.

턴 처리 순서(실패 경계): open_turn(세션 확보 + user 메시지 커밋) → run_agent →
성공 시 close_turn(assistant 메시지 + 컨텍스트 커밋). 에이전트가 실패해도 질문은
이미 남아 재시도 가능하고, 컨텍스트는 마지막 성공분이 유지된다.

동시성(ADR-22): 같은 세션에 이미 처리 중인 턴이 있으면 두 번째는 거절한다
(SessionBusyError→409). 응답 생성 중 재입력에 의한 컨텍스트 레이스를 막는다.
단일 상시 프로세스 전제라 인프로세스 락으로 충분(스케일아웃 시 Redis 분산락).

run_agent는 지연 임포트한다 — agents.llm이 임포트 시점에 load_dotenv()로 자기
리포의 .env를 os.environ에 주입하는 부작용이 있어, 백엔드 Settings가 먼저 로드되기
전에 임포트되면 그쪽 POSTGRES_* 값이 백엔드 DB 설정을 오염시킨다.
"""
import threading
from contextlib import contextmanager
from typing import Callable

from agents.schemas import (
    AgentRequest,
    AgentResponse,
    EntryContext,
    Filters,
    SessionContext,
)

from app.config import get_settings
from app.infra.db.repositories.chat_repository import ChatRepository

# 세션당 동시 1턴 — 인프로세스 락(단일 프로세스 전제).
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


class SessionBusyError(Exception):
    """같은 세션에 처리 중인 턴이 있음(응답 생성 중 재입력). 라우터가 409."""


@contextmanager
def _one_turn(session_id: str):
    with _inflight_lock:
        if session_id in _inflight:
            raise SessionBusyError(session_id)
        _inflight.add(session_id)
    try:
        yield
    finally:
        with _inflight_lock:
            _inflight.discard(session_id)


class AgentChatService:
    def __init__(self, repository: ChatRepository,
                 runner: Callable[[AgentRequest], AgentResponse] | None = None):
        self._repo = repository
        self._runner = runner          # None이면 첫 호출에 run_agent를 늦게 묶는다

    def _run(self, req: AgentRequest) -> AgentResponse:
        if self._runner is None:
            from agents.run import run_agent
            self._runner = run_agent
        return self._runner(req)

    def chat(self, *, query: str, company_id: str,
             entry_bid_id: str | None = None,
             session_id: str | None = None) -> tuple[str, AgentResponse]:
        cid = int(company_id)
        sid = session_id or self._repo.new_session_id()
        with _one_turn(sid):
            # 세션 길이 소프트캡 — 기존 세션이 상한을 넘으면 LLM을 부르지 않고
            # 새 대화를 유도한다(비용·무한 사용 방지). 새 세션은 캡 대상이 아니다.
            if session_id is not None:
                if self._repo.count_turns(sid, cid) >= get_settings().session_max_turns:
                    return sid, self._capped_response()
            # 세션 확보(소유 검증/신규 생성) + user 메시지 커밋. 반환=저장된 컨텍스트.
            ctx = self._repo.open_turn(sid, cid, query)
            req = AgentRequest(query=query, company_id=company_id,
                               entry_context=EntryContext(bid_id=entry_bid_id),
                               session_context=ctx)
            resp = self._run(req)   # 실패 시 user 메시지는 남고 가드 해제(finally)
            self._repo.close_turn(sid, resp)   # assistant + 컨텍스트 커밋
        return sid, resp

    @staticmethod
    def _capped_response() -> AgentResponse:
        """세션 길이 소프트캡 응답 — LLM 없이 새 대화 유도. 에러가 아니라 안내."""
        return AgentResponse(
            action="clarify",
            clarify_message="대화가 너무 길어졌어요. 새 대화를 시작해 주세요.",
            session_context=SessionContext(
                last_bid_ids=[], last_summary="", last_filters=Filters()),
        )
