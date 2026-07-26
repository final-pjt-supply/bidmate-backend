# -*- coding: utf-8 -*-
"""마스터 자동완성 응답 스키마.

저장/매칭의 정본은 code이고 name은 표시용이다 — 프론트는 code만 다시 보낸다.
"""
from pydantic import BaseModel


class ItemOption(BaseModel):
    item_code: str
    item_name: str | None = None


class ItemSearchResponse(BaseModel):
    """자동완성 결과. 검색어가 비면 빈 목록(전체 덤프 금지)."""

    items: list[ItemOption] = []
