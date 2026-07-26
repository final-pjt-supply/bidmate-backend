# -*- coding: utf-8 -*-
"""마스터 자동완성 유스케이스.

품목만 API로 서빙한다. 면허·지역·인력·인증은 종수가 적어 프론트가 로컬 스냅샷을
쓰고 있고(각 3.7KB~180KB), 품목만 35,171행이라 스냅샷이 불가능하다.
"""
from app.api.v1.schemas.master import ItemOption, ItemSearchResponse
from app.infra.db.repositories.master_repository import MasterRepository


class MasterService:
    def __init__(self, repository: MasterRepository):
        self._repo = repository

    def search_items(self, *, q: str, limit: int) -> ItemSearchResponse:
        rows = self._repo.search_items(q, limit=limit)
        return ItemSearchResponse(
            items=[ItemOption(item_code=c, item_name=n) for c, n in rows]
        )
