# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 조회 — company_id로 8개 섹션을 읽어 한 번에 돌려준다.

멀티테넌시: 모든 쿼리가 WHERE company_id=? 로 격리된다(토큰의 회사만).
정렬은 결정적으로(코드/record_id) — 같은 요청에 같은 순서를 보장한다.
"""
from dataclasses import dataclass

from sqlalchemy import delete, select

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

    def _all(self, model, company_id: int, *order):
        stmt = select(model).where(model.company_id == company_id).order_by(*order)
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
            # 분야별 다중 행이 생기므로 (자격, 분야)로 정렬 — 왕복 순서를 결정적으로.
            personnel=self._all(
                CompanyPersonnel,
                company_id,
                CompanyPersonnel.qual_code,
                CompanyPersonnel.field_family,
            ),
            capacity_evals=self._all(
                CompanyCapacityEval, company_id, CompanyCapacityEval.license_code
            ),
            performance_records=self._all(
                CompanyPerformanceRecord, company_id, CompanyPerformanceRecord.record_id
            ),
        )

    # 전체 테이블 순서(삭제/삽입 공용). qualification은 1:1이라 별도 취급.
    _CHILD_MODELS = (
        CompanyRegion, CompanyLicense, CompanyItem, CompanyCert,
        CompanyPersonnel, CompanyCapacityEval, CompanyPerformanceRecord,
    )

    def replace_profile(
        self,
        company_id: int,
        *,
        qualification: CompanyQualification | None,
        regions: list[CompanyRegion],
        licenses: list[CompanyLicense],
        items: list[CompanyItem],
        certs: list[CompanyCert],
        personnel: list[CompanyPersonnel],
        capacity_evals: list[CompanyCapacityEval],
        performance_records: list[CompanyPerformanceRecord],
    ) -> None:
        """회사 프로필 전체 교체(full replace) — 한 트랜잭션.

        기존 회사 행을 모두 지우고 넘어온 것으로 다시 채운다. 실패 시 전부 롤백돼
        절반만 저장되는 일이 없다(원자성). 넘어온 ORM 인스턴스는 이미 company_id·
        _name까지 채워져 있다(서비스 소관).
        """
        s = self._session
        try:
            # 삭제: 자식 + qualification 모두 회사 스코프로.
            for model in (*self._CHILD_MODELS, CompanyQualification):
                s.execute(delete(model).where(model.company_id == company_id))
            # 삽입.
            if qualification is not None:
                s.add(qualification)
            for rows in (regions, licenses, items, certs, personnel,
                         capacity_evals, performance_records):
                s.add_all(rows)
            s.commit()
        except Exception:
            # DB 제약(FK·CHECK 등) 위반 시 세션을 되돌린다 — 절반만 반영되거나
            # 다음 요청이 오염된 세션을 물려받지 않게(서비스가 IntegrityError→422).
            s.rollback()
            raise
