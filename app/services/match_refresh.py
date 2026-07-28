# -*- coding: utf-8 -*-
"""매칭 주기 갱신 (#80) — 신규 공고를 match_results에 반영한다.

match_results는 '회사 자격 × 공고 요구'의 사전계산 결과다. 회사 자격 변경은
PUT /me/profile 훅(#75)이 즉시 잡지만, 반대편인 **공고 변경**을 잡는 주체가
없었다. 공고는 5분마다 들어오는데 매칭은 회원이 프로필을 다시 저장할 때만
갱신되니 시간이 갈수록 벌어진다(실측: 5시간 뒤 라이브 공고 18건 누락).

두 모드로 돈다:
  증분(기본, 5분) — 새로 정규화된 공고 행만 UPSERT. 5분 사이 실제로 바뀌는 건
    새 공고 수십 건뿐이라, 전체 교체(회사 100곳 기준 14만 행)를 288회/일 반복해
    dead tuple·WAL을 쌓을 이유가 없다.
  전체(야간 1회) — 회사별 DELETE+INSERT. 마감이 지나 계산 대상에서 빠진 행을
    정리하고 증분이 놓친 것을 보정한다.

동시성: 세 경로(프로필 훅·증분·전체)가 모두 같은 DB 함수를 쓰고 전부 멱등이라
겹쳐 돌아도 last-writer-wins로 안전하다. Blue/Green 전환 중 두 슬롯이 잠시
함께 도는 것도 같은 이유로 무해하다.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.infra.db.repositories.match_repository import MatchRepository

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))

# 정규화 시각과 계산 시각의 경계에서 놓치는 공고를 막는 여유. 같은 초에 정규화된
# 공고가 계산 직후에 커밋되면 '이미 계산함'으로 오인될 수 있어, 하한을 조금 앞으로
# 당겨 겹치게 읽는다. 중복 계산은 UPSERT라 무해하다.
_OVERLAP = timedelta(minutes=1)


def now_kst() -> datetime:
    """DB의 시각 규약과 맞춘 naive KST(컬럼이 timestamp without time zone)."""
    return datetime.now(_KST).replace(tzinfo=None)


class MatchRefreshService:
    """한 주기 분량의 갱신을 수행한다. 스케줄링은 호출자(루프) 몫."""

    def __init__(self, repository: MatchRepository):
        self._repo = repository

    def run_once(self, *, full: bool = False) -> dict:
        """증분 또는 전체 갱신 1회. 반환은 로그용 요약.

        full=False(기본): 새로 정규화된 공고가 없으면 DB 쓰기 없이 즉시 끝낸다.
        """
        companies = self._repo.active_company_ids()
        if not companies:
            return {"mode": "full" if full else "incremental", "companies": 0,
                    "rows": 0, "skipped": "회사 없음"}

        if full:
            rows = sum(self._repo.recompute_for_company(cid) for cid in companies)
            return {"mode": "full", "companies": len(companies), "rows": rows}

        normalized_at = self._repo.latest_normalized_at()
        computed_at = self._repo.latest_computed_at()
        if normalized_at is None:
            return {"mode": "incremental", "companies": 0, "rows": 0,
                    "skipped": "정규화 데이터 없음"}
        # 계산 이력이 없으면(최초 기동) 증분의 기준점이 없다 — 전체로 한 번 채운다.
        if computed_at is None:
            rows = sum(self._repo.recompute_for_company(cid) for cid in companies)
            return {"mode": "full(최초)", "companies": len(companies), "rows": rows}
        if normalized_at <= computed_at:
            return {"mode": "incremental", "companies": 0, "rows": 0,
                    "skipped": "신규 정규화 없음"}

        since = computed_at - _OVERLAP
        rows = sum(self._repo.upsert_since(cid, since) for cid in companies)
        return {"mode": "incremental", "companies": len(companies), "rows": rows,
                "since": since.isoformat()}


async def match_refresh_loop(
    session_factory,
    *,
    interval_sec: int,
    full_hour: int,
    now_fn=now_kst,
) -> None:
    """주기 루프 — lifespan이 백그라운드 태스크로 띄운다.

    예외를 절대 밖으로 내보내지 않는다: 이 태스크가 죽으면 조용히 갱신이 멈추고,
    API는 멀쩡해 보여 알아채기 어렵다. DB 일시 장애는 다음 주기에 자연히 회복된다.
    세션은 주기마다 새로 열고 닫는다 — 상시 프로세스에서 세션을 오래 들고 있으면
    끊긴 커넥션·유휴 트랜잭션을 떠안는다.
    """
    last_full_date = None      # 야간 전체 재계산을 하루 한 번으로 묶는 기준

    while True:
        await asyncio.sleep(interval_sec)
        try:
            now = now_fn()
            full = now.hour == full_hour and last_full_date != now.date()

            def _work() -> dict:
                session = session_factory()
                try:
                    return MatchRefreshService(MatchRepository(session)).run_once(full=full)
                finally:
                    session.close()

            # DB 접근이 동기(SQLAlchemy 동기 엔진)라 이벤트 루프를 막지 않게 스레드로 뺀다.
            summary = await asyncio.to_thread(_work)

            if full:
                last_full_date = now.date()
            if summary.get("rows"):
                logger.info("매칭 갱신: %s", summary)
            else:
                logger.debug("매칭 갱신 없음: %s", summary)
        except asyncio.CancelledError:
            raise                                  # 종료 신호는 그대로 통과시킨다
        except Exception:
            logger.exception("매칭 갱신 실패 — 다음 주기에 재시도")
