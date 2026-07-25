# -*- coding: utf-8 -*-
"""GET /me/profile 응답 스키마 — 회사 자격요건 프로필 8개 섹션.

각 섹션은 ORM 행에서 바로 조립한다(from_attributes). 값은 관대하게(Optional) 둔다
— 데모/부분입력 데이터 한 건 때문에 프로필 전체 조회가 500 나면 안 된다.
`_name`은 DB에 비정규화 저장돼 있어 그대로 노출한다(마스터 조인 없음).
"""
from datetime import date

from pydantic import BaseModel, ConfigDict


class _Section(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class QualificationOut(_Section):
    company_size: str | None = None
    credit_rating: str | None = None


class RegionOut(_Section):
    region_code: str
    region_name: str | None = None
    region_type: str | None = None


class LicenseOut(_Section):
    license_code: str
    license_name: str | None = None


class ItemOut(_Section):
    item_code: str
    item_name: str | None = None
    has_direct_production: bool | None = None
    direct_prod_valid_until: date | None = None


class CertOut(_Section):
    cert_code: str
    cert_name: str | None = None
    valid_until: date | None = None


class PersonnelOut(_Section):
    qual_code: str
    qual_name: str | None = None
    headcount: int | None = None


class CapacityEvalOut(_Section):
    license_code: str
    license_name: str | None = None
    eval_amount: int | None = None
    eval_year: int | None = None


class PerformanceRecordOut(_Section):
    record_id: int
    contract_name: str | None = None
    field_code: str | None = None
    field_name: str | None = None
    contract_amt: int | None = None
    end_date: date | None = None


class ProfileResponse(BaseModel):
    """회사 자격요건 프로필 전체.

    qualification은 1:1이라 없을 수 있어 Optional(가입 직후 미입력). 나머지는
    1:N이라 없으면 빈 배열로 준다 — 프론트가 null 분기 없이 렌더한다.
    """
    company_id: str
    qualification: QualificationOut | None = None
    regions: list[RegionOut] = []
    licenses: list[LicenseOut] = []
    items: list[ItemOut] = []
    certs: list[CertOut] = []
    personnel: list[PersonnelOut] = []
    capacity_evals: list[CapacityEvalOut] = []
    performance_records: list[PerformanceRecordOut] = []
