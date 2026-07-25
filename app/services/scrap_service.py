# -*- coding: utf-8 -*-
"""스크랩 유스케이스 — 담기/빼기/목록. 목록 응답은 bids와 같은 계약을 쓴다."""
from app.api.v1.schemas.bid import BidListItem, BidListResponse
from app.infra.db.repositories.scrap_repository import ScrapRepository

PAGE_SIZE = 20   # 목록 명세 고정(bids와 동일)


class BidNotFoundError(Exception):
    """존재하지 않거나 merged가 아닌 공고를 담으려 함. 라우터가 404로 통일."""


class ScrapService:
    def __init__(self, repository: ScrapRepository):
        self._repo = repository

    def add(self, *, company_id: int, bid_id: str) -> None:
        if not self._repo.add(company_id, bid_id):
            raise BidNotFoundError(bid_id)

    def remove(self, *, company_id: int, bid_id: str) -> None:
        self._repo.remove(company_id, bid_id)

    def list_scraps(self, *, company_id: int, page: int) -> BidListResponse:
        total = self._repo.count(company_id)
        offset = (page - 1) * PAGE_SIZE
        rows = self._repo.list_page(company_id, limit=PAGE_SIZE, offset=offset)
        items = [BidListItem.model_validate(r) for r in rows]
        return BidListResponse(total=total, page=page, page_size=PAGE_SIZE, items=items)
