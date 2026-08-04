# -*- coding: utf-8 -*-
"""에이전트 대화 서비스 — 세션 왕복 규약(ADR 0005) + DB 영속화(ADR-22).

규약: 이전 턴 AgentResponse.session_context를 저장했다가 다음 턴 AgentRequest에
'수정 없이 그대로' 넣는다. 이제 인메모리가 아니라 RDS(ChatRepository)에 보관한다
— 서버 재시작·멀티턴에도 문맥이 유지되고 대화 이력이 남는다.

턴 처리 순서(실패 경계): open_turn(세션 확보 + user 메시지 커밋) → 에이전트 호출 →
성공 시 close_turn(assistant 메시지 + 컨텍스트 커밋). 에이전트가 실패해도 질문은
이미 남아 재시도 가능하고, 컨텍스트는 마지막 성공분이 유지된다.

동시성(ADR-22): 같은 세션에 이미 처리 중인 턴이 있으면 두 번째는 거절한다
(SessionBusyError→409). 응답 생성 중 재입력에 의한 컨텍스트 레이스를 막는다.
단일 상시 프로세스 전제라 인프로세스 락으로 충분(스케일아웃 시 Redis 분산락).

runner는 주입받는다. 운영 runner는 AgentServiceClient(HTTP, POST /turn) —
에이전트가 별도 프로세스로 분리되면서 in-process run_agent 직접 호출을 대체했다
(app/agents/agent_client.py). 백엔드는 에이전트 구현이 아니라 계약
(agents.schemas)만 임포트하므로 agents.llm의 import-time load_dotenv()가 백엔드
DB 설정을 오염시키던 문제도 사라졌다. 테스트는 가짜 runner를 넣어 에이전트
프로세스 없이 왕복 규약만 검증한다.
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


# 한 턴을 처리하는 호출 가능 객체. 운영은 HTTP 클라이언트, 테스트는 가짜.
Runner = Callable[[AgentRequest], AgentResponse]


class AgentChatService:
    def __init__(self, repository: ChatRepository, runner: Runner):
        self._repo = repository
        self._runner = runner

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
            resp = self._runner(req)   # 실패 시 user 메시지는 남고 가드 해제(finally)
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
