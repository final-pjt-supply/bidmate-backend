# -*- coding: utf-8 -*-
"""통계 조회 — bid_stats matview에서 (업종, 품목) 한 행을 꺼낸다.

집계 로직은 전부 matview 정의(alembic 0006)에 있다. 여기는 조회만 한다.
ORM 모델을 두지 않는 이유는 matview가 앱 소유 테이블이 아니고 컬럼이 셋뿐이라
매핑을 유지할 이득이 없어서다.
"""
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SELECT = text(
    "SELECT payload, computed_at FROM bid_stats WHERE category = :category AND tag = :tag"
)


class StatsRepository:
    def __init__(self, session: Session):
        self._session = session

    def get(self, category: str, tag: str) -> tuple[dict[str, Any], datetime] | None:
        """없으면 None. 조건 조합이 45개뿐이라 PK 조회 한 번이면 끝난다.

        matview 자체가 없어도 None이다. CD가 RUN_MIGRATIONS=false를 기본으로 두므로
        (deploy/CD.md 'Alembic 안전 규칙') 코드가 먼저 배포되고 마이그레이션이 나중에
        켜지는 구간이 반드시 생긴다. 그때 500을 내면 배포 사고로 보이지만 실제로는
        정상적인 순서다. 서비스가 503으로 매핑한다.
        """
        try:
            row = self._session.execute(
                _SELECT, {"category": category, "tag": tag}
            ).first()
        except ProgrammingError:
            # 트랜잭션이 aborted 상태로 남는다. 롤백하지 않으면 같은 세션의 후속
            # 쿼리가 전부 InFailedSqlTransaction으로 죽는다.
            self._session.rollback()
            logger.warning("bid_stats matview 없음 — 마이그레이션(0006) 미적용")
            return None
        return (row[0], row[1]) if row else None
