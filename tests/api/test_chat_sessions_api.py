# -*- coding: utf-8 -*-
"""GET /me/sessions(목록)·/me/sessions/{id}(상세) 계약 테스트.

실 SQL(company_id 격리 조회)은 실 DB 스모크로 별도 검증. 여기서는 진짜
ChatQueryService에 Fake repo를 물려 응답계약·IDOR(404)·인증·페이징을 고정한다.
"""
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_authenticated_user, get_chat_query_service
from app.infra.db.repositories.chat_repository import SessionForbiddenError
from app.main import app
from app.services.chat_query_service import ChatQueryService

CID = 9001
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_NOW = datetime(2026, 7, 28, 10, 0)


def _session(sid, title, company_id=CID):
    return SimpleNamespace(session_id=sid, company_id=company_id, title=title,
                           created_at=_NOW, updated_at=_NOW, deleted_at=None)


def _msg(role, content):
    return SimpleNamespace(role=role, content=content, response_meta=None, created_at=_NOW)


class FakeChatRepo:
    def __init__(self):
        self.data = {
            SID: (_session(SID, "대전 공고"),
                  [_msg("user", "대전 공고 알려줘"), _msg("assistant", "2건입니다.")]),
        }

    def count_sessions(self, company_id):
        return sum(1 for s, _ in self.data.values() if s.company_id == company_id)

    def list_sessions(self, company_id, *, limit, offset):
        rows = [s for s, _ in self.data.values() if s.company_id == company_id]
        return rows[offset:offset + limit]

    def get_session_messages(self, session_id, company_id):
        sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
        entry = self.data.get(sid)
        if entry is None or entry[0].company_id != company_id:
            raise SessionForbiddenError(str(session_id))
        return entry


@pytest.fixture
def sess_client():
    service = ChatQueryService(FakeChatRepo())

    def _make(company_id: str = str(CID)) -> TestClient:
        app.dependency_overrides[get_chat_query_service] = lambda: service
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id=company_id, email=None)
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_list_my_sessions(sess_client):
    body = sess_client().get("/me/sessions").json()
    assert set(body) == {"total", "page", "page_size", "items"}
    assert body["total"] == 1
    assert body["items"][0]["session_id"] == str(SID)
    assert body["items"][0]["title"] == "대전 공고"


def test_get_session_detail(sess_client):
    body = sess_client().get(f"/me/sessions/{SID}").json()
    assert body["session_id"] == str(SID)
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == "대전 공고 알려줘"


def test_other_company_session_is_404(sess_client):
    assert sess_client(company_id="99999").get(f"/me/sessions/{SID}").status_code == 404


def test_unknown_session_is_404(sess_client):
    other = uuid.UUID("22222222-2222-2222-2222-222222222222")
    assert sess_client().get(f"/me/sessions/{other}").status_code == 404


def test_invalid_uuid_is_404(sess_client):
    assert sess_client().get("/me/sessions/not-a-uuid").status_code == 404


def test_sessions_require_login(sess_client):
    sess_client()
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).get("/me/sessions").status_code == 401


def test_out_of_range_page_is_empty(sess_client):
    body = sess_client().get("/me/sessions?page=99").json()
    assert body["total"] == 1 and body["items"] == []
