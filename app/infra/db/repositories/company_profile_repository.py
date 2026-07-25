# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 조회 — company_id로 8개 섹션을 읽어 한 번에 돌려준다.

멀티테넌시: 모든 쿼리가 WHERE company_id=? 로 격리된다(토큰의 회사만).
정렬은 결정적으로(코드/record_id) — 같은 요청에 같은 순서를 보장한다.
"""
from dataclasses import dataclass

from sqlalchemy import select

from app.infra.db.models.company_profile import (
    CompanyCapacityEval,
    CompanyCert,
    CompanyItem,
    CompanyLicense,
    CompanyPerformanceRecord,
    CompanyPersonnel,
    CompanyQualification,
    CompanyRegion,
)


@dataclass
class ProfileRows:
    qualification: CompanyQualification | None
    regions: list[CompanyRegion]
    licenses: list[CompanyLicense]
    items: list[CompanyItem]
    certs: list[CompanyCert]
    personnel: list[CompanyPersonnel]
    capacity_evals: list[CompanyCapacityEval]
    performance_records: list[CompanyPerformanceRecord]


class CompanyProfileRepository:
    def __init__(self, session):
        self._session = session

    def _all(self, model, company_id: int, order):
        stmt = select(model).where(model.company_id == company_id).order_by(order)
        return list(self._session.execute(stmt).scalars().all())

    def load(self, company_id: int) -> ProfileRows:
        qual = self._session.execute(
            select(CompanyQualification).where(
                CompanyQualification.company_id == company_id
            )
        ).scalar_one_or_none()
        return ProfileRows(
            qualification=qual,
            regions=self._all(CompanyRegion, company_id, CompanyRegion.region_code),
            licenses=self._all(CompanyLicense, company_id, CompanyLicense.license_code),
            items=self._all(CompanyItem, company_id, CompanyItem.item_code),
            certs=self._all(CompanyCert, company_id, CompanyCert.cert_code),
            personnel=self._all(CompanyPersonnel, company_id, CompanyPersonnel.qual_code),
            capacity_evals=self._all(
                CompanyCapacityEval, company_id, CompanyCapacityEval.license_code
            ),
            performance_records=self._all(
                CompanyPerformanceRecord, company_id, CompanyPerformanceRecord.record_id
            ),
        )
