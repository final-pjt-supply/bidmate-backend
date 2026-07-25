# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 조회 유스케이스.

프로필 행이 하나도 없어도(가입 직후) 200 + 빈 섹션으로 응답한다 — 프론트가
'프로필 미입력' 화면을 자연스럽게 그리게. 404는 회사 자체가 없을 때만이고,
그건 인증 단계(get_authenticated_user)가 이미 걸러준다.
"""
from app.api.v1.schemas.profile import (
    CapacityEvalOut,
    CertOut,
    ItemOut,
    LicenseOut,
    PerformanceRecordOut,
    PersonnelOut,
    ProfileResponse,
    QualificationOut,
    RegionOut,
)
from app.infra.db.repositories.company_profile_repository import (
    CompanyProfileRepository,
)


class CompanyProfileService:
    def __init__(self, repository: CompanyProfileRepository):
        self._repo = repository

    def get_profile(self, *, company_id: int) -> ProfileResponse:
        rows = self._repo.load(company_id)
        qual = (
            QualificationOut.model_validate(rows.qualification)
            if rows.qualification is not None
            else None
        )
        return ProfileResponse(
            company_id=str(company_id),
            qualification=qual,
            regions=[RegionOut.model_validate(r) for r in rows.regions],
            licenses=[LicenseOut.model_validate(r) for r in rows.licenses],
            items=[ItemOut.model_validate(r) for r in rows.items],
            certs=[CertOut.model_validate(r) for r in rows.certs],
            personnel=[PersonnelOut.model_validate(r) for r in rows.personnel],
            capacity_evals=[
                CapacityEvalOut.model_validate(r) for r in rows.capacity_evals
            ],
            performance_records=[
                PerformanceRecordOut.model_validate(r)
                for r in rows.performance_records
            ],
        )
