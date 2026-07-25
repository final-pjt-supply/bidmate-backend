# -*- coding: utf-8 -*-
"""GET /me/profile 계약 테스트 (라우터↔서비스↔DTO 조립).

실제 SQL(8테이블 company_id 격리 조회)은 로컬/운영 실 DB로 별도 검증한다. 여기서는
진짜 CompanyProfileService에 Fake repo를 물려 조립·응답계약·인증필수·회사격리를
in-memory로 고정한다. 복합키 여러 행이 뭉개지지 않는지도 확인한다.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user, get_company_profile_service
from app.infra.db.repositories.company_profile_repository import ProfileRows
from app.main import app
from app.services.company_profile_service import CompanyProfileService

COMPANY_WITH_DATA = 9001


def _rows_for(company_id: int) -> ProfileRows:
    """데이터 있는 회사(9001)면 채워서, 아니면 빈 프로필을 돌려준다."""
    if company_id != COMPANY_WITH_DATA:
        return ProfileRows(None, [], [], [], [], [], [], [])
    return ProfileRows(
        qualification=SimpleNamespace(company_size="medium", credit_rating="BBB"),
        regions=[SimpleNamespace(region_code="11", region_name="서울특별시", region_type="본점")],
        licenses=[
            SimpleNamespace(license_code="0037", license_name="전기공사업"),
            SimpleNamespace(license_code="0040", license_name="정보통신공사업"),
        ],
        items=[SimpleNamespace(item_code="1234", item_name="노트북",
                               has_direct_production=True, direct_prod_valid_until=None)],
        certs=[SimpleNamespace(cert_code="C1", cert_name="ISO9001", valid_until=None)],
        personnel=[SimpleNamespace(qual_code="Q1", qual_name="정보처리기사", headcount=3)],
        capacity_evals=[SimpleNamespace(license_code="0037", license_name="전기공사업",
                                        eval_amount=1_000_000, eval_year=2025)],
        performance_records=[SimpleNamespace(record_id=1, contract_name="A 구축사업",
                                             field_code="0037", field_name="전기공사업",
                                             contract_amt=5_000_000, end_date=None)],
    )


class FakeProfileRepo:
    def load(self, company_id: int) -> ProfileRows:
        return _rows_for(company_id)


@pytest.fixture
def profile_client():
    service = CompanyProfileService(FakeProfileRepo())

    def _make(company_id: str = str(COMPANY_WITH_DATA)) -> TestClient:
        app.dependency_overrides[get_company_profile_service] = lambda: service
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id=company_id, email=None
        )
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_get_profile_full_contract(profile_client):
    c = profile_client()
    r = c.get("/me/profile")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "company_id", "qualification", "regions", "licenses", "items",
        "certs", "personnel", "capacity_evals", "performance_records",
    }
    assert body["company_id"] == str(COMPANY_WITH_DATA)
    assert body["qualification"] == {"company_size": "medium", "credit_rating": "BBB"}


def test_composite_key_rows_not_collapsed(profile_client):
    """한 회사의 면허 2건이 ORM 아이덴티티로 뭉개지면 안 된다(복합 PK)."""
    c = profile_client()
    licenses = c.get("/me/profile").json()["licenses"]
    assert len(licenses) == 2
    assert {l["license_code"] for l in licenses} == {"0037", "0040"}


def test_empty_profile_is_200_with_empty_sections(profile_client):
    c = profile_client(company_id="12345")   # 프로필 미입력 회사
    r = c.get("/me/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["qualification"] is None
    assert body["regions"] == [] and body["licenses"] == []


def test_profile_requires_login(profile_client):
    profile_client()   # 서비스만 override
    app.dependency_overrides.pop(get_authenticated_user, None)
    client = TestClient(app)
    assert client.get("/me/profile").status_code == 401


def test_company_isolation(profile_client):
    """인증된 company_id가 서비스로 그대로 전달돼, 다른 회사엔 데이터가 안 샌다."""
    def as_company(cid: str):
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id=cid, email=None
        )

    c = profile_client(str(COMPANY_WITH_DATA))
    assert c.get("/me/profile").json()["licenses"]        # 9001은 데이터 있음
    as_company("99999")
    assert c.get("/me/profile").json()["licenses"] == []  # 다른 회사엔 없음
