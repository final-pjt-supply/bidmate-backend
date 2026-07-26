# -*- coding: utf-8 -*-
"""표준코드 마스터 조회 — code 집합 → {code: name} 매핑.

프로필 입력 시 이걸로 name을 채우고, 동시에 '반환에 빠진 code = 마스터에 없는 코드'로
검증한다(서비스가 그 차집합을 422로 돌린다). 빈 입력엔 빈 dict.
"""
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infra.db.models.master import (
    CertMaster,
    ItemCodeMaster,
    LicenseMaster,
    PersonnelGradeMaster,
    RegionMaster,
)


class MasterRepository:
    def __init__(self, session: Session):
        self._session = session

    def _lookup(self, code_col, name_col, codes: Iterable[str]) -> dict[str, str | None]:
        codes = list(set(codes))
        if not codes:
            return {}
        rows = self._session.execute(
            select(code_col, name_col).where(code_col.in_(codes))
        ).all()
        return {c: n for c, n in rows}

    def region_names(self, codes: Iterable[str]) -> dict[str, str | None]:
        return self._lookup(RegionMaster.region_code, RegionMaster.region_name, codes)

    def license_names(self, codes: Iterable[str]) -> dict[str, str | None]:
        return self._lookup(LicenseMaster.license_code, LicenseMaster.license_name, codes)

    def item_names(self, codes: Iterable[str]) -> dict[str, str | None]:
        return self._lookup(ItemCodeMaster.item_code, ItemCodeMaster.item_name, codes)

    def personnel_names(self, codes: Iterable[str]) -> dict[str, str | None]:
        return self._lookup(
            PersonnelGradeMaster.qual_code, PersonnelGradeMaster.qual_name, codes
        )

    def cert_names(self, codes: Iterable[str]) -> dict[str, str | None]:
        return self._lookup(CertMaster.cert_code, CertMaster.cert_name, codes)

    def search_items(self, q: str, *, limit: int) -> list[tuple[str, str | None]]:
        """품목 자동완성 — 품명 부분일치(대소문자 무시).

        10자리(세부품명)만 돌려준다. 8자리는 상위 분류라, 회사가 그걸로 등록하면
        10자리를 요구하는 공고와 코드가 어긋난다(ItemCodeMaster 주석 참조).

        q가 비면 빈 목록 — 35,171행을 통째로 흘리지 않는다. 정렬은 짧은 이름 우선
        ("노트북컴퓨터"가 "융복합노트북컴퓨터"보다 먼저)이고, 동률은 코드로 안정화한다.
        """
        term = q.strip()
        if not term:
            return []
        pattern = f"%{term}%"
        stmt = (
            select(ItemCodeMaster.item_code, ItemCodeMaster.item_name)
            .where(
                func.length(ItemCodeMaster.item_code) == 10,
                ItemCodeMaster.is_active.is_(True),
                ItemCodeMaster.item_name.ilike(pattern),
            )
            .order_by(
                func.length(ItemCodeMaster.item_name),
                ItemCodeMaster.item_code,
            )
            .limit(limit)
        )
        return [(c, n) for c, n in self._session.execute(stmt).all()]
