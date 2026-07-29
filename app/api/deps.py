# -*- coding: utf-8 -*-
"""FastAPI 의존성. 인증 진입점과 계층 조립(session→repository→service)을 여기 모은다.

★ 인증 자리 확보(지금 로직 미구현, 자리만):
  나중에 JWT(Cognito) 인증이 모든 엔드포인트에 붙는다. 그때 뜯어고치지 않도록,
  엔드포인트가 '현재 사용자(company_id)'를 받는 통로를 지금 만들어 두고 통과시킨다.
  회사별 점수 정렬(sort=score)도 같은 통로(CurrentUser.company_id)로 들어온다.
"""
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agents.chat_rate_limit import RateLimited
from app.agents.chat_rate_limit import guard as _chat_guard
from app.agents.chat_service import AgentChatService
from app.config import get_settings
from app.infra.auth.cognito import TokenError, verify_id_token
from app.infra.db.repositories.bid_repository import BidRepository
from app.infra.db.repositories.company_profile_repository import (
    CompanyProfileRepository,
)
from app.infra.db.repositories.chat_repository import ChatRepository
from app.infra.db.repositories.chat_usage_repository import (
    ChatUsageRepository,
    seconds_to_kst_midnight,
)
from app.infra.db.repositories.company_repository import CompanyRepository
from app.infra.db.repositories.master_repository import MasterRepository
from app.infra.db.repositories.match_repository import MatchRepository
from app.infra.db.repositories.recommendation_repository import (
    RecommendationRepository,
)
from app.infra.db.repositories.scrap_repository import ScrapRepository
from app.infra.db.session import get_session
from app.infra.search.recommendation_search import RecommendationSearch
from app.infra.s3.event_sink import get_event_sink
from app.services.bid_service import BidService
from app.services.chat_query_service import ChatQueryService
from app.services.company_profile_service import CompanyProfileService
from app.services.event_service import EventService
from app.services.master_service import MasterService
from app.services.match_service import MatchService
from app.services.recommendation_service import RecommendationService
from app.services.scrap_service import ScrapService


@dataclass(frozen=True)
class CurrentUser:
    """현재 요청 주체. 멀티테넌시 격리(WHERE company_id=?)와 회사별 정렬의 키.

    company_id가 None이면 '비로그인'이다 — 공고 목록/상세처럼 로그인 없이도 보이는
    화면이 있어 익명을 허용한다. 로그인이 필수인 엔드포인트는
    get_authenticated_user를 쓴다(없으면 401).
    """
    company_id: str | None = None
    email: str | None = None


def get_db() -> Iterator[Session]:
    yield from get_session()


def _bearer_token(request: Request) -> str | None:
    """Authorization: Bearer <token> 에서 토큰만 꺼낸다. 없으면 None."""
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> CurrentUser:
    """★ 인증 진입점 — '선택적' 인증.

    토큰이 없으면 익명(company_id=None)으로 통과시킨다. 공고 조회는 비로그인도
    가능해야 하기 때문(홈 화면). 토큰이 있으면 검증해서 company_id를 채운다.
    토큰이 있는데 유효하지 않으면 조용히 익명 처리하지 않고 401로 막는다 —
    만료된 토큰을 들고 계속 익명 결과를 받으면 로그인이 풀린 걸 눈치채기 어렵다.
    """
    settings = get_settings()

    # 로컬 개발 우회: 토큰 없이 고정 회사로 통과. 운영에선 auth_disabled=False.
    if settings.auth_disabled:
        return CurrentUser(company_id=settings.dev_company_id or None)

    token = _bearer_token(request)
    if token is None:
        return CurrentUser()  # 비로그인 — 허용

    identity = _verify_or_401(token)
    repo = CompanyRepository(db)

    # 탈퇴한 계정의 토큰은 만료 전까지 살아 있다. 막지 않으면 아래 JIT 생성이
    # 회사를 다시 만들어 탈퇴가 무효가 된다.
    if repo.is_withdrawn_sub(identity.sub):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="탈퇴한 계정입니다",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # email로 기존 회사에 계정을 잇는 경로(get_or_create)는 이메일 소유가 증명된
    # 경우에만 안전하다. 검증되지 않은 이메일은 없는 것으로 취급해 링크·저장을 막는다
    # → companies.email 컬럼에는 항상 검증된 값만 들어가고, 남의 회사 가로채기가 불가능.
    # (표시명 폴백에는 원본 email을 그대로 써도 무방하다 — 저장이 아니라 화면용.)
    verified_email = identity.email if identity.email_verified else None
    company = repo.get_or_create(
        cognito_sub=identity.sub,
        email=verified_email,
        # 회사명은 가입 시 별도로 받기 전까지 이메일 앞부분을 임시 표시명으로 둔다.
        name=identity.name or (identity.email or "").split("@")[0] or "이름 미등록",
    )
    return CurrentUser(company_id=str(company.id), email=company.email)


def get_authenticated_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """로그인이 '필수'인 엔드포인트용. 비로그인이면 401."""
    if current_user.company_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def _verify_or_401(token: str):
    try:
        return verify_id_token(token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_bid_service(db: Session = Depends(get_db)) -> BidService:
    # 상세(get_bid)에서 매칭 판정을 붙이려면 MatchRepository도 필요하다.
    return BidService(BidRepository(db), MatchRepository(db))


def get_scrap_service(db: Session = Depends(get_db)) -> ScrapService:
    return ScrapService(ScrapRepository(db))


def get_company_profile_service(
    db: Session = Depends(get_db),
) -> CompanyProfileService:
    # 입력(PUT)은 마스터에서 name을 채우고 코드를 검증하므로 MasterRepository도 준다.
    return CompanyProfileService(CompanyProfileRepository(db), MasterRepository(db))


def get_match_service(db: Session = Depends(get_db)) -> MatchService:
    return MatchService(MatchRepository(db))


@lru_cache(maxsize=1)
def get_recommendation_search() -> RecommendationSearch:
    """검색 어댑터는 프로세스당 하나만 만든다.

    요청마다 새로 만들면 안에 든 httpx 커넥션 풀과 임베딩 캐시가 매번 버려져,
    커넥션 재사용도 캐시 적중도 일어나지 않는다. 설정(get_settings)이 이미
    프로세스 수명 캐시라 어댑터도 같은 수명으로 둔다. 상태는 커넥션·캐시뿐이고
    회사별 데이터를 담지 않으므로 요청 간 공유해도 격리가 깨지지 않는다.
    """
    settings = get_settings()
    return RecommendationSearch(
        opensearch_url=settings.opensearch_url,
        opensearch_user=settings.opensearch_user,
        opensearch_password=settings.opensearch_password,
        index_name=settings.opensearch_index_name,
        verify_certs=settings.opensearch_verify_certs,
        cf_account_id=settings.cf_account_id,
        cf_api_token=settings.cf_api_token,
        cf_model=settings.cf_embedding_model,
    )


def get_recommendation_service(
    db: Session = Depends(get_db),
) -> RecommendationService:
    return RecommendationService(
        RecommendationRepository(db), get_recommendation_search()
    )


def get_master_service(db: Session = Depends(get_db)) -> MasterService:
    return MasterService(MasterRepository(db))


def get_event_service() -> EventService:
    # 이벤트는 RDS가 아니라 S3(NDJSON)로 적재 — 분석을 운영 DB에서 분리. DB 세션 불필요.
    return EventService(get_event_sink())


def get_agent_chat_service(db: Session = Depends(get_db)) -> AgentChatService:
    # 세션/대화는 RDS 영속(ADR-22) — ChatRepository로 세션 컨텍스트·메시지 저장.
    return AgentChatService(ChatRepository(db))


def get_chat_query_service(db: Session = Depends(get_db)) -> ChatQueryService:
    return ChatQueryService(ChatRepository(db))


def get_chat_usage_repo(db: Session = Depends(get_db)) -> ChatUsageRepository:
    return ChatUsageRepository(db)


def _too_many(retry_after: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(retry_after)},
    )


def enforce_chat_limits(
    current_user: CurrentUser = Depends(get_authenticated_user),
    usage: ChatUsageRepository = Depends(get_chat_usage_repo),
) -> Iterator[None]:
    """/agent/chat 레이트리밋 — 회사당 분당·동시성·일일(Bedrock 비용 방어).

    순서: 분당(인메모리 슬라이딩) → 동시성 획득(인메모리) → 일일(RDS 원자 증가) →
    처리 → 해제. 동시성 슬롯을 잡은 뒤엔 try/finally로 반드시 해제한다. 검증실패
    (422 공백 등)는 이 의존성 실행 전 단계라 카운트 안 되고, 에이전트 실패(502)는
    이미 카운트된 뒤라 포함된다(실패 턴도 비용을 유발했으므로).
    """
    settings = get_settings()
    cid = int(current_user.company_id)
    retry_msg = "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
    try:
        _chat_guard.check_minute(cid, settings.rate_limit_per_min)
    except RateLimited as exc:
        raise _too_many(exc.retry_after, retry_msg)
    try:
        _chat_guard.acquire(cid, settings.chat_concurrency_max)
    except RateLimited as exc:
        raise _too_many(exc.retry_after, retry_msg)
    try:
        if usage.incr_and_get(cid) > settings.chat_daily_max:
            raise _too_many(
                seconds_to_kst_midnight(),
                "오늘 사용 가능한 횟수를 초과했습니다. 내일 다시 시도해 주세요.",
            )
        yield
    finally:
        _chat_guard.release(cid)
