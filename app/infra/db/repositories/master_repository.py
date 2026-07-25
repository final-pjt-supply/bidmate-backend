# -*- coding: utf-8 -*-
"""표준코드 마스터 조회 — code 집합 → {code: name} 매핑.

프로필 입력 시 이걸로 name을 채우고, 동시에 '반환에 빠진 code = 마스터에 없는 코드'로
검증한다(서비스가 그 차집합을 422로 돌린다). 빈 입력엔 빈 dict.
"""
from collections.abc import Iterable

from sqlalchemy import select
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
