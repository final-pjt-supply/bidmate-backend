# -*- coding: utf-8 -*-
"""POST/DELETE/GET /me/scraps 계약 테스트 (라우터↔서비스).

실제 SQL(bid_id 해석·ON CONFLICT·JOIN)은 로컬 실 DB로 별도 검증했다. 여기서는
멱등·404·인증필수·회사격리·응답계약을 in-memory 대역으로 고정한다.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user, get_scrap_service
from app.api.v1.schemas.bid import BidListItem, BidListResponse
from app.main import app
from app.services.scrap_service import BidNotFoundError, PAGE_SIZE

# 대역이 "존재하는 공고"로 인정하는 집합. 그 외 bid_id는 404.
KNOWN_BIDS = {"bid_a", "bid_b", "bid_c"}


class FakeScrapRepo:
    """(company_id, bid_id) 쌍의 집합으로 스크랩을 흉내낸다."""

    def __init__(self):
        self.store: set[tuple[int, str]] = set()

    def add(self, company_id: int, bid_id: str) -> bool:
        if bid_id not in KNOWN_BIDS:
            return False
        self.store.add((company_id, bid_id))  # set이라 중복은 자연히 멱등
        return True

    def remove(self, company_id: int, bid_id: str) -> None:
        self.store.discard((company_id, bid_id))  # 없어도 에러 없음

    def count(self, company_id: int) -> int:
        return sum(1 for c, _ in self.store if c == company_id)

    def list_page(self, company_id: int, *, limit: int, offset: int):
        ids = [b for c, b in sorted(self.store) if c == company_id]
        # BidListItem이 from_attributes로 읽을 수 있게 최소 속성만 가진 객체를 만든다.
        rows = [
            type("Row", (), {
                "bid_id": b, "bid_ntce_nm": b, "dminstt_nm": None,
                "bid_category": "servc", "sucsfbid_mthd_nm": None,
                "bid_clse_dt": None, "bdgt_amt": None, "bid_prtcpt_lmt_yn": None,
                "match_score": None,
            })()
            for b in ids
        ]
        return rows[offset : offset + limit]


class FakeScrapService:
    """진짜 ScrapService와 같은 정책(없는 공고 404)을 대역 repo 위에서 재현."""

    def __init__(self, repo: FakeScrapRepo):
        self._repo = repo

    def add(self, *, company_id, bid_id):
        if not self._repo.add(company_id, bid_id):
            raise BidNotFoundError(bid_id)

    def remove(self, *, company_id, bid_id):
        self._repo.remove(company_id, bid_id)

    def list_scraps(self, *, company_id, page):
        total = self._repo.count(company_id)
        offset = (page - 1) * PAGE_SIZE
        rows = self._repo.list_page(company_id, limit=PAGE_SIZE, offset=offset)
        items = [BidListItem.model_validate(r) for r in rows]
        return BidListResponse(total=total, page=page, page_size=PAGE_SIZE, items=items)


@pytest.fixture
def scrap_client():
    """로그인 회사 id를 지정해 뜬 TestClient. 같은 서비스 인스턴스를 공유한다."""
    repo = FakeScrapRepo()
    service = FakeScrapService(repo)

    def _make(company_id: str = "42") -> TestClient:
        app.dependency_overrides[get_scrap_service] = lambda: service
        app.dependency_overrides[get_authenticated_user] = lambda: type(
            "U", (), {"company_id": company_id, "email": None}
        )()
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_add_then_list(scrap_client):
    c = scrap_client()
    assert c.post("/me/scraps", json={"bid_id": "bid_a"}).status_code == 204
    body = c.get("/me/scraps").json()
    assert body["total"] == 1
    assert body["items"][0]["bid_id"] == "bid_a"
    # 응답 계약은 /bids 목록과 동일해야 한다(프론트가 BidCard 재사용).
    assert set(body) == {"total", "page", "page_size", "items"}


def test_add_is_idempotent(scrap_client):
    c = scrap_client()
    c.post("/me/scraps", json={"bid_id": "bid_a"})
    assert c.post("/me/scraps", json={"bid_id": "bid_a"}).status_code == 204  # 두 번째도 204
    assert c.get("/me/scraps").json()["total"] == 1  # 수 안 늘어남


def test_add_unknown_bid_is_404(scrap_client):
    c = scrap_client()
    assert c.post("/me/scraps", json={"bid_id": "does-not-exist"}).status_code == 404


def test_remove(scrap_client):
    c = scrap_client()
    c.post("/me/scraps", json={"bid_id": "bid_a"})
    assert c.delete("/me/scraps/bid_a").status_code == 204
    assert c.get("/me/scraps").json()["total"] == 0


def test_remove_is_idempotent(scrap_client):
    c = scrap_client()
    # 담은 적 없어도 204
    assert c.delete("/me/scraps/bid_a").status_code == 204


def test_scraps_require_login(scrap_client):
    """인증 없이 접근하면 401. get_authenticated_user override를 걷어내고 확인."""
    scrap_client()  # 서비스만 override, 인증 override는 지운다
    app.dependency_overrides.pop(get_authenticated_user, None)
    client = TestClient(app)
    assert client.get("/me/scraps").status_code == 401
    assert client.post("/me/scraps", json={"bid_id": "bid_a"}).status_code == 401


def test_company_isolation(scrap_client):
    """한 회사가 담은 스크랩이 다른 회사 목록에 보이면 안 된다.

    인증 override가 전역이라 요청 사이에 회사를 바꿔가며 확인한다(동시 두 신원 대신).
    """
    def as_company(cid):
        app.dependency_overrides[get_authenticated_user] = lambda: type(
            "U", (), {"company_id": cid, "email": None}
        )()

    c = scrap_client("42")            # 서비스 override + 초기 회사 42
    c.post("/me/scraps", json={"bid_id": "bid_a"})   # 42가 담음
    as_company("99")
    assert c.get("/me/scraps").json()["total"] == 0  # 99에게는 안 보임
    as_company("42")
    assert c.get("/me/scraps").json()["total"] == 1  # 42에게는 보임


def test_empty_page_out_of_range(scrap_client):
    c = scrap_client()
    c.post("/me/scraps", json={"bid_id": "bid_a"})
    body = c.get("/me/scraps?page=99").json()
    assert body["total"] == 1 and body["items"] == []
