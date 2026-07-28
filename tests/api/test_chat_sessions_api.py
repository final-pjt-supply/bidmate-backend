# -*- coding: utf-8 -*-
"""GET·DELETE /me/sessions 계약 테스트 — 목록/상세/대화방 삭제/마지막 턴 취소.

실 SQL(company_id 격리 조회)은 실 DB 스모크로 별도 검증. 여기서는 진짜
ChatQueryService에 Fake repo를 물려 응답계약·IDOR(404)·인증·페이징을 고정한다.
Fake는 실 repository의 불변식(deleted_at IS NULL 게이트, 턴 한 쌍 삭제,
문맥 비우기)을 파이썬으로 미러링한다.
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
                           created_at=_NOW, updated_at=_NOW, deleted_at=None,
                           session_context={"last_summary": "이전 문맥"})


def _msg(role, content):
    return SimpleNamespace(role=role, content=content, response_meta=None, created_at=_NOW)


class FakeChatRepo:
    def __init__(self):
        self.data = {
            SID: (_session(SID, "대전 공고"),
                  [_msg("user", "대전 공고 알려줘"), _msg("assistant", "2건입니다.")]),
        }

    def _live(self, company_id):
        """삭제되지 않은 내 세션만 — 실 repository의 deleted_at IS NULL 게이트."""
        return [s for s, _ in self.data.values()
                if s.company_id == company_id and s.deleted_at is None]

    def _entry(self, session_id, company_id):
        """없음/남의 것/이미 삭제됨을 한곳에서 404 대상으로 통일."""
        sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
        entry = self.data.get(sid)
        if entry is None or entry[0].company_id != company_id or entry[0].deleted_at is not None:
            raise SessionForbiddenError(str(session_id))
        return entry

    def count_sessions(self, company_id):
        return len(self._live(company_id))

    def list_sessions(self, company_id, *, limit, offset):
        return self._live(company_id)[offset:offset + limit]

    def get_session_messages(self, session_id, company_id):
        return self._entry(session_id, company_id)

    def soft_delete_session(self, session_id, company_id):
        self._entry(session_id, company_id)[0].deleted_at = _NOW

    def delete_last_turn(self, session_id, company_id):
        sess, msgs = self._entry(session_id, company_id)
        if not msgs:
            return 0
        # 끝이 assistant면 직전 user까지 한 쌍, user만 남았으면(에이전트 실패) 한 건.
        n = 2 if (msgs[-1].role == "assistant" and len(msgs) > 1
                  and msgs[-2].role == "user") else 1
        del msgs[len(msgs) - n:]
        sess.session_context = None       # 되돌릴 수 없어 비운다(실 repository와 동일)
        return n


@pytest.fixture
def sess_client():
    repo = FakeChatRepo()
    service = ChatQueryService(repo)

    def _make(company_id: str = str(CID)) -> TestClient:
        app.dependency_overrides[get_chat_query_service] = lambda: service
        app.dependency_overrides[get_authenticated_user] = lambda: SimpleNamespace(
            company_id=company_id, email=None)
        return TestClient(app)

    _make.repo = repo          # 테스트가 저장 상태(문맥·삭제표시)를 직접 확인할 수 있게
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


# ── 대화방 삭제 ──────────────────────────────────────────────
def test_delete_session_disappears_from_list_and_detail(sess_client):
    c = sess_client()
    assert c.delete(f"/me/sessions/{SID}").status_code == 204
    body = c.get("/me/sessions").json()
    assert body["total"] == 0 and body["items"] == []
    assert c.get(f"/me/sessions/{SID}").status_code == 404


def test_delete_session_keeps_the_row(sess_client):
    """소프트 삭제 — 행은 남고 deleted_at만 찍힌다(실삭제는 회사 탈퇴 CASCADE)."""
    sess_client().delete(f"/me/sessions/{SID}")
    assert sess_client.repo.data[SID][0].deleted_at is not None


def test_delete_session_twice_is_404(sess_client):
    c = sess_client()
    assert c.delete(f"/me/sessions/{SID}").status_code == 204
    assert c.delete(f"/me/sessions/{SID}").status_code == 404


def test_delete_other_company_session_is_404(sess_client):
    assert sess_client(company_id="99999").delete(f"/me/sessions/{SID}").status_code == 404
    # IDOR 시도가 실제로 아무것도 지우지 않았는지 — 주인에게는 그대로 보인다.
    assert sess_client().get(f"/me/sessions/{SID}").status_code == 200


def test_delete_unknown_session_is_404(sess_client):
    other = uuid.UUID("22222222-2222-2222-2222-222222222222")
    assert sess_client().delete(f"/me/sessions/{other}").status_code == 404


def test_delete_invalid_uuid_is_404(sess_client):
    assert sess_client().delete("/me/sessions/not-a-uuid").status_code == 404


def test_delete_requires_login(sess_client):
    sess_client()
    app.dependency_overrides.pop(get_authenticated_user, None)
    assert TestClient(app).delete(f"/me/sessions/{SID}").status_code == 401


# ── 마지막 턴 취소 ───────────────────────────────────────────
def test_delete_last_turn_removes_pair_and_clears_context(sess_client):
    c = sess_client()
    assert c.delete(f"/me/sessions/{SID}/last-turn").status_code == 204
    assert c.get(f"/me/sessions/{SID}").json()["messages"] == []
    # 문맥을 비우지 않으면 지운 말을 봇이 계속 기억한다.
    assert sess_client.repo.data[SID][0].session_context is None


def test_delete_last_turn_keeps_earlier_turns(sess_client):
    sess_client.repo.data[SID][1].extend(
        [_msg("user", "두번째 질문"), _msg("assistant", "두번째 답변")])
    c = sess_client()
    assert c.delete(f"/me/sessions/{SID}/last-turn").status_code == 204
    contents = [m["content"] for m in c.get(f"/me/sessions/{SID}").json()["messages"]]
    assert contents == ["대전 공고 알려줘", "2건입니다."]


def test_delete_last_turn_drops_lone_user_message(sess_client):
    """에이전트가 죽어 질문만 남은 턴 — 그 한 건만 지운다(앞 턴은 보존)."""
    sess_client.repo.data[SID][1].append(_msg("user", "답을 못 받은 질문"))
    c = sess_client()
    assert c.delete(f"/me/sessions/{SID}/last-turn").status_code == 204
    contents = [m["content"] for m in c.get(f"/me/sessions/{SID}").json()["messages"]]
    assert contents == ["대전 공고 알려줘", "2건입니다."]


def test_delete_last_turn_on_empty_session_is_404(sess_client):
    c = sess_client()
    assert c.delete(f"/me/sessions/{SID}/last-turn").status_code == 204
    assert c.delete(f"/me/sessions/{SID}/last-turn").status_code == 404


def test_delete_last_turn_on_deleted_session_is_404(sess_client):
    c = sess_client()
    c.delete(f"/me/sessions/{SID}")
    assert c.delete(f"/me/sessions/{SID}/last-turn").status_code == 404


def test_delete_last_turn_of_other_company_is_404(sess_client):
    assert sess_client(company_id="99999").delete(
        f"/me/sessions/{SID}/last-turn").status_code == 404
