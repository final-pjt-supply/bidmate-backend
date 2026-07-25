# -*- coding: utf-8 -*-
"""company_bid_scraps 조회/쓰기.

공고를 프론트는 bid_id로 다루지만 이 테이블은 (bid_ntce_no, bid_ntce_ord)로
저장한다(bid_table PK). 그래서 담기/빼기는 먼저 bid_id → (no, ord)로 해석한다.

노출 게이트(qual_status='merged')는 목록 조회에도 건다 — 스크랩 이후 pending으로
바뀐 공고가 목록에 새어나오지 않게.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.domain.enums import QualStatus
from app.infra.db.models.bid import Bid
from app.infra.db.models.scrap import CompanyBidScrap

_MERGED = QualStatus.MERGED.value


class ScrapRepository:
    def __init__(self, session):
        self._session = session

    def _resolve(self, bid_id: str) -> tuple[str, str] | None:
        """bid_id → (bid_ntce_no, bid_ntce_ord). merged 공고만. 없으면 None."""
        row = self._session.execute(
            select(Bid.bid_ntce_no, Bid.bid_ntce_ord)
            .where(Bid.bid_id == bid_id, Bid.qual_status == _MERGED)
        ).first()
        return (row[0], row[1]) if row else None

    def add(self, company_id: int, bid_id: str) -> bool:
        """담기. 존재하는 merged 공고면 저장(중복은 무시)하고 True.
        없는 공고면 아무것도 안 하고 False(라우터가 404)."""
        resolved = self._resolve(bid_id)
        if resolved is None:
            return False
        no, ord_ = resolved
        # 이미 담긴 경우 조용히 통과 — 두 번 눌러도 에러 안 남(멱등).
        stmt = pg_insert(CompanyBidScrap).values(
            company_id=company_id, bid_ntce_no=no, bid_ntce_ord=ord_
        ).on_conflict_do_nothing()
        self._session.execute(stmt)
        self._session.commit()
        return True

    def remove(self, company_id: int, bid_id: str) -> None:
        """빼기. 담겨있지 않아도 에러 없음(멱등)."""
        resolved = self._resolve(bid_id)
        if resolved is None:
            return
        no, ord_ = resolved
        self._session.execute(
            delete(CompanyBidScrap).where(
                CompanyBidScrap.company_id == company_id,
                CompanyBidScrap.bid_ntce_no == no,
                CompanyBidScrap.bid_ntce_ord == ord_,
            )
        )
        self._session.commit()

    def count(self, company_id: int) -> int:
        """내 스크랩 총 건수. 사라진(비-merged) 공고는 제외 — 목록과 수를 맞춘다."""
        stmt = (
            select(func.count())
            .select_from(CompanyBidScrap)
            .join(
                Bid,
                (Bid.bid_ntce_no == CompanyBidScrap.bid_ntce_no)
                & (Bid.bid_ntce_ord == CompanyBidScrap.bid_ntce_ord),
            )
            .where(CompanyBidScrap.company_id == company_id, Bid.qual_status == _MERGED)
        )
        return self._session.execute(stmt).scalar_one()

    def list_page(self, company_id: int, *, limit: int, offset: int) -> list[Bid]:
        """내 스크랩 한 페이지 — 담은 최신순. 마감 공고도 포함한다
        (내가 담은 걸 조용히 숨기지 않는다)."""
        stmt = (
            select(Bid)
            .join(
                CompanyBidScrap,
                (CompanyBidScrap.bid_ntce_no == Bid.bid_ntce_no)
                & (CompanyBidScrap.bid_ntce_ord == Bid.bid_ntce_ord),
            )
            .where(CompanyBidScrap.company_id == company_id, Bid.qual_status == _MERGED)
            .order_by(CompanyBidScrap.created_at.desc(), Bid.bid_id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(stmt).scalars().all())
