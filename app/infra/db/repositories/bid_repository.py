# -*- coding: utf-8 -*-
"""bid_table 조회 쿼리. 쿼리 실행만 담당하고 비즈니스 판단(정렬 폴백/페이지 계산)은
services에 둔다.

노출 게이트(qual_status='merged')는 모든 조회 메서드에 고정으로 박는다 — API가
pending/partial/failed를 흘리면 안 되는 건 도메인 불변식이라, 호출부 실수로 빠질 수
없게 여기서 강제한다.
"""
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import QualStatus
from app.infra.db.models.bid import Bid

_MERGED = QualStatus.MERGED.value


def _escape_like(value: str) -> str:
    """LIKE 와일드카드 이스케이프.

    사용자가 친 `%`나 `_`가 패턴 문법으로 해석되면 "100%"를 찾을 때 엉뚱한 결과가
    나온다. 이스케이프 문자 자체(`\\`)를 먼저 처리해야 이중 이스케이프가 안 생긴다.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class BidRepository:
    def __init__(self, session: Session):
        self._session = session

    def _merged_base(self):
        """merged 게이트가 걸린 공통 베이스 select."""
        return select(Bid).where(Bid.qual_status == _MERGED)

    def count(
        self,
        *,
        category: str | None,
        ntce_dt_from: datetime | None,
        ntce_dt_to: datetime | None,
        clse_after: datetime | None = None,
    ) -> int:
        """필터 적용 후 총 건수(응답 total)."""
        stmt = select(func.count()).select_from(Bid).where(Bid.qual_status == _MERGED)
        stmt = self._apply_filters(stmt, category, ntce_dt_from, ntce_dt_to, clse_after)
        return self._session.execute(stmt).scalar_one()

    def list_page(
        self,
        *,
        category: str | None,
        ntce_dt_from: datetime | None,
        ntce_dt_to: datetime | None,
        limit: int,
        offset: int,
        clse_after: datetime | None = None,
    ) -> list[Bid]:
        """마감 임박순(deadline) 한 페이지.

        정렬은 bid_clse_dt ASC + NULLS LAST — 마감일 없는 공고가 메인 홈 상단을
        먹지 않게 한다. 안정 정렬을 위해 bid_id로 tie-break.

        # TODO(company-scoped sort): sort=score가 실제 동작하게 되면 여기가 아니라
        #   호출부(service)에서 current_user.company_id로 match_results를 조인해
        #   회사별 점수순으로 정렬하는 별도 경로가 붙는다. 그 조인 쿼리를 이 클래스에
        #   list_page_by_score(company_id, ...)로 추가하고, service가 분기한다.
        """
        stmt = self._merged_base()
        stmt = self._apply_filters(stmt, category, ntce_dt_from, ntce_dt_to, clse_after)
        stmt = (
            stmt.order_by(Bid.bid_clse_dt.asc().nulls_last(), Bid.bid_id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(stmt).scalars().all())

    # ── 검색 전용 경로 ────────────────────────────────────────────────
    # 홈·추천이 공유하는 count/list_page와 분리해 둔다. 검색은 앞으로 필터가 계속
    # 늘어나는데(공고명·금액·낙찰방법·지역…), 공용 경로에 얹으면 필터 하나가 잘못돼도
    # 홈·추천이 함께 깨진다. merged 게이트·마감 필터가 두 벌이 되는 비용은 감수한다.

    def search_count(
        self,
        *,
        category: str | None,
        clse_after: datetime | None = None,
        q: str | None = None,
    ) -> int:
        """검색 필터 적용 후 총 건수."""
        stmt = select(func.count()).select_from(Bid).where(Bid.qual_status == _MERGED)
        stmt = self._apply_search_filters(stmt, category, clse_after, q)
        return self._session.execute(stmt).scalar_one()

    def search_page(
        self,
        *,
        category: str | None,
        sort: str,
        limit: int,
        offset: int,
        clse_after: datetime | None = None,
        q: str | None = None,
    ) -> list[Bid]:
        """검색 결과 한 페이지.

        정렬 두 가지 — 어느 쪽이든 bid_id로 tie-break해 페이지 경계에서 행이 중복·
        누락되지 않게 한다(불안정 정렬이면 2페이지에 1페이지 행이 다시 나올 수 있다).
          * deadline: 마감 임박순. bid_clse_dt ASC + NULLS LAST(마감 미정이 상단을 먹지 않게)
          * recent  : 최신 등록순. bid_ntce_dt DESC + NULLS LAST(게시일 미상은 뒤로)
        """
        stmt = self._merged_base()
        stmt = self._apply_search_filters(stmt, category, clse_after, q)
        if sort == "recent":
            order = (Bid.bid_ntce_dt.desc().nulls_last(), Bid.bid_id.asc())
        else:
            order = (Bid.bid_clse_dt.asc().nulls_last(), Bid.bid_id.asc())
        stmt = stmt.order_by(*order).limit(limit).offset(offset)
        return list(self._session.execute(stmt).scalars().all())

    @staticmethod
    def _apply_search_filters(stmt, category, clse_after=None, q=None):
        """검색 경로 필터. 후속 이슈에서 금액·낙찰방법·지역 등이 여기 붙는다."""
        if category is not None:
            stmt = stmt.where(Bid.bid_category == category)
        # 마감 지난 공고 제외. 마감일 NULL은 "아직 안 닫힘"으로 보고 남긴다(공용 경로와 동일).
        if clse_after is not None:
            stmt = stmt.where(or_(Bid.bid_clse_dt >= clse_after, Bid.bid_clse_dt.is_(None)))
        # 공고명·수요기관·공고기관 부분일치(대소문자 무시). 셋 중 하나만 걸려도 결과에 포함.
        #
        # 인덱스 없이 순차 스캔이다. 노출 대상이 1400여 건 규모라 지금은 충분하고,
        # 느려지면 pg_trgm GIN 인덱스를 별도로 붙인다(공유 운영 DB라 협의 필요).
        if q:
            pattern = f"%{_escape_like(q)}%"
            stmt = stmt.where(
                or_(
                    Bid.bid_ntce_nm.ilike(pattern, escape="\\"),
                    Bid.dminstt_nm.ilike(pattern, escape="\\"),
                    Bid.ntce_instt_nm.ilike(pattern, escape="\\"),
                )
            )
        return stmt

    def get_by_bid_id(self, bid_id: str) -> Bid | None:
        """merged인 단건. 존재하지 않거나 merged가 아니면 None(호출부가 404로 통일)."""
        stmt = self._merged_base().where(Bid.bid_id == bid_id)
        return self._session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def _apply_filters(stmt, category, ntce_dt_from, ntce_dt_to, clse_after=None):
        if category is not None:
            stmt = stmt.where(Bid.bid_category == category)
        # today: 공고게시일(bid_ntce_dt) 기준 KST 오늘 [from, to) 반개구간.
        if ntce_dt_from is not None:
            stmt = stmt.where(Bid.bid_ntce_dt >= ntce_dt_from)
        if ntce_dt_to is not None:
            stmt = stmt.where(Bid.bid_ntce_dt < ntce_dt_to)
        # 마감 지난 공고 제외(추천 페이지). 마감일이 없는(NULL) 공고는 "아직 안 닫힘"
        # 으로 보고 남긴다 — 날짜 미상이라고 숨기지 않는다.
        if clse_after is not None:
            stmt = stmt.where(or_(Bid.bid_clse_dt >= clse_after, Bid.bid_clse_dt.is_(None)))
        return stmt
