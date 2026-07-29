# -*- coding: utf-8 -*-
"""GET /me/profile 계약 테스트 (라우터↔서비스↔DTO 조립).

실제 SQL(8테이블 company_id 격리 조회)은 로컬/운영 실 DB로 별도 검증한다. 여기서는
진짜 CompanyProfileService에 Fake repo를 물려 조립·응답계약·인증필수·회사격리를
in-memory로 고정한다. 복합키 여러 행이 뭉개지지 않는지도 확인한다.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    get_authenticated_user,
    get_company_profile_service,
    get_match_service,
)
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


# ── 입력(PUT /me/profile) ─────────────────────────────────────────────
class FakeMasterRepo:
    REGION = {"11": "서울특별시"}
    LICENSE = {"0037": "전기공사업", "0040": "정보통신공사업"}
    ITEM = {"1234": "노트북"}
    CERT = {"C1": "ISO9001"}
    PERSONNEL = {"Q1": "정보처리기사"}

    def _pick(self, table, codes):
        return {c: table[c] for c in set(codes) if c in table}

    def region_names(self, codes):
        return self._pick(self.REGION, codes)

    def license_names(self, codes):
        return self._pick(self.LICENSE, codes)

    def item_names(self, codes):
        return self._pick(self.ITEM, codes)

    def cert_names(self, codes):
        return self._pick(self.CERT, codes)

    def personnel_names(self, codes):
        return self._pick(self.PERSONNEL, codes)


class FakeWriteRepo:
    def __init__(self):
        self.saved: ProfileRows | None = None

    def replace_profile(self, company_id, *, qualification, regions, licenses,
                        items, certs, personnel, capacity_evals, performance_records):
        for i, r in enumerate(performance_records, start=1):
            r.record_id = i   # DB 시퀀스(record_id) 흉내
        self.saved = ProfileRows(
            qualification, regions, licenses, items, certs, personnel,
            capacity_evals, performance_records,
        )

    def load(self, company_id):
        return self.saved or ProfileRows(None, [], [], [], [], [], [], [])


class FakeMatchService:
    """저장 후 재계산 훅 검증용 — 실제 DB를 안 건드린다."""

    def __init__(self):
        self.recomputed: list[int] = []
        self.raise_on_call = False

    def recompute_for_company(self, company_id):
        if self.raise_on_call:
            raise RuntimeError("recompute boom")
        self.recomputed.append(company_id)
        return 0


@pytest.fixture
def write_client():
    service = CompanyProfileService(FakeWriteRepo(), FakeMasterRepo())
    match_fake = FakeMatchService()

    def _make(company_id: str = "9001") -> TestClient:
        app.dependency_overrides[get_company_profile_service] = lambda: service
        app.dependency_overrides[get_match_service] = lambda: match_fake
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id=company_id, email=None
        )
        return TestClient(app)

    _make.match_fake = match_fake
    yield _make
    app.dependency_overrides.clear()


def test_put_triggers_match_recompute(write_client):
    """★ 자격 저장 성공 시 그 회사 매칭 재계산이 호출된다(신선도)."""
    c = write_client()
    r = c.put("/me/profile", json={"licenses": [{"license_code": "0037"}]})
    assert r.status_code == 200
    assert write_client.match_fake.recomputed == [9001]   # 토큰의 company_id


def test_put_succeeds_even_if_recompute_fails(write_client):
    """재계산이 터져도 저장은 성공(200) — 저장은 이미 커밋, 매칭은 배치가 채운다."""
    write_client.match_fake.raise_on_call = True
    c = write_client()
    r = c.put("/me/profile", json={})
    assert r.status_code == 200


def test_put_fills_names_from_master(write_client):
    """클라는 code만 보내고 서버가 마스터에서 name을 채운다."""
    c = write_client()
    body = {
        "qualification": {"company_size": "medium"},
        "regions": [{"region_code": "11", "region_type": "hq"}],
        "licenses": [{"license_code": "0037"}],
    }
    r = c.put("/me/profile", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["regions"][0]["region_name"] == "서울특별시"
    assert data["licenses"][0]["license_name"] == "전기공사업"
    assert data["qualification"]["company_size"] == "medium"


def test_put_empty_profile_ok(write_client):
    """모든 섹션 선택 — 빈 프로필도 200."""
    c = write_client()
    r = c.put("/me/profile", json={})
    assert r.status_code == 200
    assert r.json()["licenses"] == [] and r.json()["qualification"] is None


def test_put_too_many_items_is_422(write_client):
    """배열 상한 초과 — 요청 파싱 단계에서 422(서비스 도달 전, 남용 방어)."""
    c = write_client()
    r = c.put("/me/profile", json={
        "regions": [{"region_code": "11", "region_type": "hq"}] * 31   # max_length=30
    })
    assert r.status_code == 422


def test_put_unknown_code_is_422(write_client):
    c = write_client()
    r = c.put("/me/profile", json={"licenses": [{"license_code": "9999"}]})
    assert r.status_code == 422


def test_put_duplicate_code_is_422(write_client):
    c = write_client()
    r = c.put("/me/profile", json={
        "licenses": [{"license_code": "0037"}, {"license_code": "0037"}]
    })
    assert r.status_code == 422


def test_put_forbids_client_supplied_name(write_client):
    """서버가 채우는 name을 클라가 밀어넣으면 거부(extra=forbid)."""
    c = write_client()
    r = c.put("/me/profile", json={
        "regions": [{"region_code": "11", "region_type": "hq", "region_name": "해킹"}]
    })
    assert r.status_code == 422


def test_put_missing_notnull_infield_is_422(write_client):
    """region_type은 NOT NULL → 행을 넣으면 필수."""
    c = write_client()
    r = c.put("/me/profile", json={"regions": [{"region_code": "11"}]})
    assert r.status_code == 422


def test_put_requires_login(write_client):
    write_client()
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).put("/me/profile", json={}).status_code == 401
