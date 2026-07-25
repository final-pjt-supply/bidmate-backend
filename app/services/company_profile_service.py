# -*- coding: utf-8 -*-
"""회사 자격요건 프로필 유스케이스 — 조회 + 입력(전체 교체).

입력은 클라가 code만 보내고 서버가 마스터에서 name을 채운다(신뢰경계 서버).
마스터에 없는 code는 저장 전에 걸러 422로 돌린다 — 쓰레기 코드가 들어가지 않게.
모든 섹션은 선택이다(빈 프로필도 정상). 저장 전 검증만 통과하면 한 트랜잭션으로
전부 교체한다(company_profile_repository.replace_profile).
"""
from sqlalchemy.exc import IntegrityError

from app.api.v1.schemas.profile import (
    CapacityEvalOut,
    CertOut,
    ItemOut,
    LicenseOut,
    PerformanceRecordOut,
    PersonnelOut,
    ProfileResponse,
    ProfileUpsertRequest,
    QualificationOut,
    RegionOut,
)
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
from app.infra.db.repositories.company_profile_repository import (
    CompanyProfileRepository,
)
from app.infra.db.repositories.master_repository import MasterRepository


class ProfileValidationError(Exception):
    """저장 거부(마스터에 없는 코드/섹션 내 코드 중복). 라우터가 422로 통일."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class CompanyProfileService:
    def __init__(
        self,
        repository: CompanyProfileRepository,
        master_repository: MasterRepository | None = None,
    ):
        self._repo = repository
        self._master = master_repository

    # ── 조회 ──────────────────────────────────────────────────────────
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

    # ── 입력(전체 교체) ────────────────────────────────────────────────
    def save_profile(
        self, *, company_id: int, payload: ProfileUpsertRequest
    ) -> ProfileResponse:
        # 섹션 내 코드 중복은 테이블 PK(company_id, code) 위반 → 미리 422로 막는다.
        self._reject_duplicate_codes(payload)

        # license_master는 licenses·capacity_evals·실적(field_code) 세 곳이 함께 쓴다
        # (field_code FK가 license_master를 가리킨다).
        license_codes = (
            [x.license_code for x in payload.licenses]
            + [x.license_code for x in payload.capacity_evals]
            + [r.field_code for r in payload.performance_records if r.field_code]
        )
        region_map = self._master.region_names([r.region_code for r in payload.regions])
        license_map = self._master.license_names(license_codes)
        item_map = self._master.item_names([i.item_code for i in payload.items])
        cert_map = self._master.cert_names([c.cert_code for c in payload.certs])
        personnel_map = self._master.personnel_names(
            [p.qual_code for p in payload.personnel]
        )

        # 마스터에 없는 코드 수집 → 있으면 저장 안 하고 422.
        missing = self._collect_missing(
            payload, region_map, license_map, item_map, cert_map, personnel_map
        )
        if missing:
            raise ProfileValidationError(f"마스터에 없는 코드: {missing}")

        qualification = (
            CompanyQualification(
                company_id=company_id,
                company_size=payload.qualification.company_size,
                credit_rating=payload.qualification.credit_rating,
            )
            if payload.qualification is not None
            else None
        )
        self._save_or_422(
            company_id,
            qualification=qualification,
            regions=[
                CompanyRegion(
                    company_id=company_id, region_code=r.region_code,
                    region_name=region_map.get(r.region_code), region_type=r.region_type,
                )
                for r in payload.regions
            ],
            licenses=[
                CompanyLicense(
                    company_id=company_id, license_code=x.license_code,
                    license_name=license_map.get(x.license_code),
                )
                for x in payload.licenses
            ],
            items=[
                CompanyItem(
                    company_id=company_id, item_code=i.item_code,
                    item_name=item_map.get(i.item_code),
                    has_direct_production=i.has_direct_production,
                    direct_prod_valid_until=i.direct_prod_valid_until,
                )
                for i in payload.items
            ],
            certs=[
                CompanyCert(
                    company_id=company_id, cert_code=c.cert_code,
                    cert_name=cert_map.get(c.cert_code), valid_until=c.valid_until,
                )
                for c in payload.certs
            ],
            personnel=[
                CompanyPersonnel(
                    company_id=company_id, qual_code=p.qual_code,
                    qual_name=personnel_map.get(p.qual_code), headcount=p.headcount,
                )
                for p in payload.personnel
            ],
            capacity_evals=[
                CompanyCapacityEval(
                    company_id=company_id, license_code=e.license_code,
                    license_name=license_map.get(e.license_code),
                    eval_amount=e.eval_amount, eval_year=e.eval_year,
                )
                for e in payload.capacity_evals
            ],
            performance_records=[
                # field_code는 license_master 참조 → field_name도 거기서 채운다
                # (record_id는 시퀀스 자동).
                CompanyPerformanceRecord(
                    company_id=company_id, contract_name=r.contract_name,
                    field_code=r.field_code,
                    field_name=license_map.get(r.field_code) if r.field_code else None,
                    contract_amt=r.contract_amt, end_date=r.end_date,
                )
                for r in payload.performance_records
            ],
        )
        return self.get_profile(company_id=company_id)

    def _save_or_422(self, company_id: int, **sections) -> None:
        """DTO·마스터 검증을 통과했어도 남은 DB 제약(예상 못한 FK/CHECK) 위반은
        500 대신 422로 규약화한다. 정상 경로에선 안 걸린다(사전 검증이 대부분 잡음)."""
        try:
            self._repo.replace_profile(company_id, **sections)
        except IntegrityError as exc:
            raise ProfileValidationError(
                "저장 중 DB 제약 위반 — 코드/값을 확인해주세요"
            ) from exc

    @staticmethod
    def _reject_duplicate_codes(payload: ProfileUpsertRequest) -> None:
        sections = {
            "regions": [r.region_code for r in payload.regions],
            "licenses": [x.license_code for x in payload.licenses],
            "items": [i.item_code for i in payload.items],
            "certs": [c.cert_code for c in payload.certs],
            "personnel": [p.qual_code for p in payload.personnel],
            "capacity_evals": [e.license_code for e in payload.capacity_evals],
        }
        for name, codes in sections.items():
            if len(codes) != len(set(codes)):
                raise ProfileValidationError(f"{name} 섹션에 중복 코드가 있습니다")

    @staticmethod
    def _collect_missing(
        payload, region_map, license_map, item_map, cert_map, personnel_map
    ) -> list[str]:
        missing: list[str] = []
        missing += [r.region_code for r in payload.regions if r.region_code not in region_map]
        missing += [x.license_code for x in payload.licenses if x.license_code not in license_map]
        missing += [i.item_code for i in payload.items if i.item_code not in item_map]
        missing += [c.cert_code for c in payload.certs if c.cert_code not in cert_map]
        missing += [p.qual_code for p in payload.personnel if p.qual_code not in personnel_map]
        missing += [
            e.license_code for e in payload.capacity_evals if e.license_code not in license_map
        ]
        # 실적 field_code도 license_master 참조 → 같은 맵으로 검증(값이 있을 때만).
        missing += [
            r.field_code for r in payload.performance_records
            if r.field_code and r.field_code not in license_map
        ]
        # 순서 유지 + 중복 제거
        return list(dict.fromkeys(missing))
