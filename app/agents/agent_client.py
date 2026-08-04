# -*- coding: utf-8 -*-
"""에이전트 HTTP 클라이언트 — POST {agent_base_url}/turn.

에이전트가 같은 EC2의 별도 프로세스(루프백 8010, 자체 nginx blue/green)로 분리되면서
ADR 0005의 '같은 프로세스에 라이브러리로 임베드'는 여기서 뒤집힌다. 백엔드는
`agents.run`을 더 이상 임포트하지 않고 **계약(`agents.schemas`)만 공유**한다.

바뀌는 것:
- Bedrock 블로킹이 백엔드 uvicorn 스레드풀을 점유하지 않는다(에이전트 프로세스 몫).
- 에이전트를 백엔드 재배포 없이 배포할 수 있다(에이전트 레포의 blue/green CD가
  이 순간부터 실제 트래픽 경로가 된다).
- `agents.llm`의 import-time `load_dotenv()`가 백엔드 프로세스 환경을 오염시키던
  문제도 같이 사라진다 — `agents.schemas`는 부작용이 없어 최상단 임포트해도 된다.

바뀌지 않는 것: 세션 왕복 규약은 그대로 AgentChatService가 소유한다. 에이전트는
stateless라 session_context가 요청·응답으로만 오간다(에이전트 레포 deploy/CD.md).
"""
import logging
from functools import lru_cache

import httpx
from agents.schemas import AgentRequest, AgentResponse

from app.config import get_settings

logger = logging.getLogger(__name__)

_TURN_PATH = "/turn"
# 실패 로그에 실을 응답 본문 길이 상한. 에이전트 500 본문이 길 수 있어 자른다.
_ERROR_BODY_CHARS = 500


class AgentServiceClient:
    """AgentRequest → POST /turn → AgentResponse. AgentChatService의 runner로 꽂힌다.

    실패는 삼키지 않고 그대로 올린다(연결 실패·타임아웃·비2xx·본문 파싱 실패 모두).
    라우터(app/api/v1/agents.py)가 이를 502 + 재시도 안내로 규약화한다.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __call__(self, req: AgentRequest) -> AgentResponse:
        # mode="json"이어야 Region(StrEnum) 같은 계약 타입이 JSON 원시값으로 나간다.
        resp = self._client.post(_TURN_PATH, json=req.model_dump(mode="json"))
        if resp.is_error:
            # raise_for_status는 본문을 안 싣는다. 422(계약 불일치)·5xx의 실제 원인은
            # 본문에 있으므로 여기서 한 번 남긴다 — 없으면 502만 보고 원인을 못 찾는다.
            logger.warning(
                "agent %s%s returned %s: %s",
                self._client.base_url, _TURN_PATH, resp.status_code,
                resp.text[:_ERROR_BODY_CHARS],
            )
            resp.raise_for_status()
        # 계약 위반(필드 누락 등)은 여기서 ValidationError로 터진다 — 반쯤 빈 응답을
        # 세션에 저장해 다음 턴까지 오염시키는 것보다 낫다.
        return AgentResponse.model_validate(resp.json())

    def close(self) -> None:
        self._client.close()


@lru_cache(maxsize=1)
def get_agent_client() -> AgentServiceClient:
    """앱 수명 동안 하나의 httpx.Client(세션 스토어·이벤트 sink와 같은 싱글턴 패턴).

    sync 라우트라 uvicorn 스레드풀에서 동시에 불린다. httpx.Client는 스레드 안전하고,
    하나를 공유해야 커넥션 풀이 살아 있어 매 턴 TCP 핸드셰이크를 새로 하지 않는다.
    """
    settings = get_settings()
    timeout = httpx.Timeout(
        settings.agent_timeout_sec, connect=settings.agent_connect_timeout_sec
    )
    logger.info("agent client → %s (timeout %ss)",
                settings.agent_base_url, settings.agent_timeout_sec)
    return AgentServiceClient(
        httpx.Client(base_url=settings.agent_base_url, timeout=timeout)
    )
