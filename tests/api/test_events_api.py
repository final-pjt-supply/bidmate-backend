# -*- coding: utf-8 -*-
"""POST /events 계약 테스트.

DB 없이 in-memory repo로 정책/검증을 고정한다: enum 검증, event_type 서버 파생,
company_id 미수신(신원은 서버가 정함), KST naive created_at, extra 필드 거부.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_event_service
from app.infra.db.models.user_event import UserEvent
from app.main import app
from app.services.event_service import EventService


class FakeEventRepository:
    """insert만 있는 append-only 대역. 저장된 UserEvent를 캡처한다."""

    def __init__(self):
        self.saved: list[UserEvent] = []

    def insert(self, event: UserEvent) -> None:
        self.saved.append(event)


@pytest.fixture
def client_and_repo():
    repo = FakeEventRepository()
    app.dependency_overrides[get_event_service] = lambda: EventService(repo)
    yield TestClient(app), repo
    app.dependency_overrides.clear()


def _payload(**overrides) -> dict:
    base = dict(
        anonymous_id="anon-123",
        visit_id="11111111-1111-1111-1111-111111111111",
        event_name="bid_card_clicked",
        page="home",
        properties={"position": 3, "sort": "deadline"},
        device_type="desktop",
    )
    base.update(overrides)
    return base


def test_collect_event_202_and_derives_type(client_and_repo):
    client, repo = client_and_repo
    res = client.post("/events", json=_payload())
    assert res.status_code == 202
    assert len(repo.saved) == 1
    ev = repo.saved[0]
    assert ev.event_name == "bid_card_clicked"
    assert ev.event_type == "click"          # 서버가 event_name에서 파생
    assert ev.company_id is None             # 인증 스텁 → null(클라가 못 보냄)
    assert ev.anonymous_id == "anon-123"
    assert ev.properties == {"position": 3, "sort": "deadline"}
    # created_at은 서버가 각인한 KST naive.
    assert ev.created_at is not None and ev.created_at.tzinfo is None


def test_page_view_type_derived(client_and_repo):
    client, repo = client_and_repo
    client.post("/events", json=_payload(event_name="home_viewed"))
    assert repo.saved[0].event_type == "page_view"


def test_v1_added_events_accepted_and_typed(client_and_repo):
    # 프론트 추가 4종도 수집되고 event_type이 파생된다.
    client, repo = client_and_repo
    client.post("/events", json=_payload(event_name="login_completed"))
    client.post("/events", json=_payload(event_name="bid_external_link_clicked"))
    client.post("/events", json=_payload(event_name="bid_bookmarked", properties={"on": True}))
    client.post("/events", json=_payload(event_name="search_submitted", properties={"query_len": 12}))
    types = {e.event_name: e.event_type for e in repo.saved}
    assert types["login_completed"] == "action"
    assert types["bid_external_link_clicked"] == "click"
    assert types["bid_bookmarked"] == "action"
    assert types["search_submitted"] == "action"


def test_unknown_event_name_is_422(client_and_repo):
    client, _ = client_and_repo
    res = client.post("/events", json=_payload(event_name="bogus_event"))
    assert res.status_code == 422


def test_client_cannot_send_identity_or_extra_422(client_and_repo):
    # company_id는 DTO에 없는 필드 → extra='forbid'로 거부(신원 사칭 차단).
    client, _ = client_and_repo
    res = client.post("/events", json=_payload(company_id=999))
    assert res.status_code == 422


def test_missing_required_field_is_422(client_and_repo):
    client, _ = client_and_repo
    p = _payload()
    del p["anonymous_id"]
    assert client.post("/events", json=p).status_code == 422


def test_malformed_visit_id_is_422(client_and_repo):
    client, _ = client_and_repo
    res = client.post("/events", json=_payload(visit_id="not-a-uuid"))
    assert res.status_code == 422
