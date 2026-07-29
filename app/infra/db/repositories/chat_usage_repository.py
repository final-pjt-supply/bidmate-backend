# -*- coding: utf-8 -*-
"""챗 일일 호출 카운터 — 회사별 하루 호출 수(레이트리밋 일일 상한).

지속 저장(RDS)이라 재배포에도 안 리셋된다(인메모리 일일 카운터는 하루 여러 번의
배포에 매번 0이 돼 무의미). UPSERT로 원자 증가 — 동시 요청도 안전하게 합산된다.
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

_KST = timezone(timedelta(hours=9))

# INSERT ... ON CONFLICT DO UPDATE로 (company_id, 오늘) 행을 원자적으로 +1 하고
# 갱신된 값을 돌려받는다. 여러 요청이 동시에 와도 DB가 직렬화해 정확히 합산.
_INCR_SQL = text(
    "INSERT INTO chat_daily_usage (company_id, usage_date, call_count) "
    "VALUES (:cid, :d, 1) "
    "ON CONFLICT (company_id, usage_date) "
    "DO UPDATE SET call_count = chat_daily_usage.call_count + 1 "
    "RETURNING call_count"
)


def today_kst() -> date:
    return datetime.now(_KST).date()


def seconds_to_kst_midnight() -> int:
    """다음 KST 자정까지 남은 초 — 일일 상한 초과 시 Retry-After로 준다."""
    now = datetime.now(_KST)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


class ChatUsageRepository:
    def __init__(self, session: Session):
        self._s = session

    def incr_and_get(self, company_id: int) -> int:
        """오늘 호출 수를 +1 하고 새 값을 반환. 자기 트랜잭션으로 커밋한다."""
        count = self._s.execute(
            _INCR_SQL, {"cid": company_id, "d": today_kst()}
        ).scalar_one()
        self._s.commit()
        return count
