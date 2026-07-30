# -*- coding: utf-8 -*-
"""Cognito 인증 강화 — email_verified 가드와 AUTH_DISABLED 운영 가드."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api import deps
from app.config import Settings
from app.infra.auth.cognito import CognitoIdentity, _claim_true


# ── email_verified 클레임 정규화 ────────────────────────────────────
@pytest.mark.parametrize(
    "value, expected",
    [
        (True, True),
        ("true", True),
        (False, False),
        ("false", False),   # 문자열 "false"가 truthy로 새지 않아야 한다
        (None, False),
        ("", False),
        (0, False),
    ],
)
def test_claim_true_normalizes(value, expected):
    assert _claim_true(value) is expected


# ── get_current_user: 미검증 이메일은 회사 링크에 쓰지 않는다 ────────
class _FakeCompanyRepo:
    """get_or_create가 실제로 받은 email을 붙잡아 두는 대역."""

    last_email = "SENTINEL"
    withdrawn = False               # is_withdrawn_sub 반환값(테스트가 켠다)
    get_or_create_called = False    # 탈퇴 차단 시 호출되지 않아야 함을 검증

    def __init__(self, db):
        pass

    def is_withdrawn_sub(self, sub):
        return _FakeCompanyRepo.withdrawn

    def get_or_create(self, *, cognito_sub, email, name):
        _FakeCompanyRepo.get_or_create_called = True
        _FakeCompanyRepo.last_email = email
        return SimpleNamespace(id=1, email=email)


class _FakeRequest:
    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}"}


def _patch(monkeypatch, *, email_verified: bool, withdrawn: bool = False):
    monkeypatch.setattr(deps, "CompanyRepository", _FakeCompanyRepo)
    monkeypatch.setattr(
        deps,
        "verify_id_token",
        lambda token: CognitoIdentity(
            sub="sub-1", email="victim@corp.com", name=None, email_verified=email_verified
        ),
    )
    _FakeCompanyRepo.last_email = "SENTINEL"
    _FakeCompanyRepo.withdrawn = withdrawn
    _FakeCompanyRepo.get_or_create_called = False


def test_unverified_email_is_not_passed_to_company_link(monkeypatch):
    """email_verified=False면 email을 None으로 넘겨 링크·저장을 막는다(테넌트 탈취 차단)."""
    _patch(monkeypatch, email_verified=False)
    user = deps.get_current_user(_FakeRequest("tok"), db=None)
    assert _FakeCompanyRepo.last_email is None
    assert user.company_id == "1"


def test_verified_email_is_passed_through(monkeypatch):
    """email_verified=True면 정상적으로 email을 넘긴다(기존 동작 유지)."""
    _patch(monkeypatch, email_verified=True)
    deps.get_current_user(_FakeRequest("tok"), db=None)
    assert _FakeCompanyRepo.last_email == "victim@corp.com"


def test_withdrawn_account_token_is_401_and_not_resurrected(monkeypatch):
    """★ 탈퇴 계정의 살아있는 토큰은 401로 막고, JIT 생성으로 회사를 되살리지 않는다.

    막지 않으면 만료 전까지 살아있는 탈퇴 토큰이 get_or_create로 companies 행을
    다시 만들어 탈퇴가 무효화된다(QA H1 — 이 분기에 회귀 안전망이 없었다)."""
    _patch(monkeypatch, email_verified=True, withdrawn=True)
    with pytest.raises(HTTPException) as exc:
        deps.get_current_user(_FakeRequest("tok"), db=None)
    assert exc.value.status_code == 401
    assert "탈퇴" in exc.value.detail
    assert _FakeCompanyRepo.get_or_create_called is False   # 회사 부활 안 됨


# ── AUTH_DISABLED 운영 가드 ─────────────────────────────────────────
@pytest.mark.parametrize("slot", ["blue", "green", "prod", "PRODUCTION"])
def test_auth_disabled_blocks_startup_in_production(slot):
    """운영 슬롯에서 인증 비활성화면 Settings 생성 자체가 실패한다(fail-fast)."""
    with pytest.raises(ValidationError):
        Settings(auth_disabled=True, deployment_slot=slot)


@pytest.mark.parametrize("slot", ["local", "dev", "LOCAL", "Dev"])
def test_auth_disabled_allowed_in_dev(slot):
    """local/dev 슬롯에서는 인증 비활성화가 허용된다(대소문자 무시)."""
    settings = Settings(auth_disabled=True, deployment_slot=slot)
    assert settings.auth_disabled is True


# 운영 슬롯이 정상 기동하려면 Cognito 설정 + 실제 CORS 오리진이 모두 필요하다.
_PROD_OK = dict(
    auth_disabled=False,
    deployment_slot="blue",
    cognito_user_pool_id="pool-1",
    cognito_client_id="cid-1",
    cors_origins="https://bidmate.example.com",
)


def test_full_production_config_is_fine():
    """인증·Cognito·CORS가 모두 갖춰지면 운영 슬롯이어도 정상 기동한다."""
    settings = Settings(**_PROD_OK)
    assert settings.is_production_slot is True
    assert settings.auth_configured is True


@pytest.mark.parametrize("slot", ["blue", "green", "prod", "PRODUCTION"])
def test_cognito_unconfigured_blocks_startup_in_production(slot):
    """운영 슬롯인데 Cognito 미설정이면 기동 실패(로그인 503을 스모크가 못 잡는 갭 방어, QA M1)."""
    with pytest.raises(ValidationError):
        Settings(**{**_PROD_OK, "deployment_slot": slot,
                    "cognito_user_pool_id": "", "cognito_client_id": ""})


@pytest.mark.parametrize("cors", [
    "http://localhost:5173,http://localhost:3000",   # 기본값 그대로
    "http://127.0.0.1:3000",
    "",                                              # 비어 있음
])
def test_localhost_only_cors_blocks_startup_in_production(cors):
    """운영 슬롯인데 CORS가 localhost뿐이면 기동 실패(프론트 브라우저 호출 전면 차단 방어, QA M2)."""
    with pytest.raises(ValidationError):
        Settings(**{**_PROD_OK, "cors_origins": cors})


@pytest.mark.parametrize("slot", ["local", "dev"])
def test_dev_slot_skips_production_guards(slot):
    """local/dev 슬롯은 Cognito·CORS 미설정이어도 기동한다(개발 편의)."""
    settings = Settings(deployment_slot=slot, cognito_user_pool_id="",
                        cognito_client_id="", cors_origins="http://localhost:5173")
    assert settings.is_production_slot is False


# ── 인증 실패 응답에 내부 상세가 새지 않는다 (#92) ──────────────────
from fastapi import HTTPException  # noqa: E402

from app.infra.auth.cognito import TokenError, TokenServiceError  # noqa: E402

_LEAKY = "Invalid audience: expected 43m2oj... / Signature has expired"


def test_token_error_detail_is_not_leaked(monkeypatch, caplog):
    """401 본문에 내부 예외 문자열이 들어가면 안 된다 — 사유는 로그로만."""
    monkeypatch.setattr(
        deps, "verify_id_token", lambda t: (_ for _ in ()).throw(TokenError(_LEAKY))
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(HTTPException) as e:
            deps._verify_or_401("bad-token")

    assert e.value.status_code == 401
    assert _LEAKY not in e.value.detail          # 응답엔 없고
    assert "Signature" not in e.value.detail
    assert "audience" not in e.value.detail
    assert _LEAKY in caplog.text                  # 로그엔 있다
    assert e.value.headers.get("WWW-Authenticate") == "Bearer"


def test_jwks_failure_is_503_not_401(monkeypatch, caplog):
    """JWKS 조회 실패는 우리 쪽 장애 — 멀쩡한 사용자를 401로 내보내면 안 된다."""
    monkeypatch.setattr(
        deps,
        "verify_id_token",
        lambda t: (_ for _ in ()).throw(TokenServiceError("JWKS 조회 실패: timeout")),
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(HTTPException) as e:
            deps._verify_or_401("good-token")

    assert e.value.status_code == 503
    assert "timeout" not in e.value.detail        # 상세는 응답에 안 싣는다
    assert "JWKS 조회 실패: timeout" in caplog.text


def test_connection_error_maps_to_service_error(monkeypatch):
    """PyJWT의 JWKS 연결 실패가 TokenServiceError로 분류되는지(경계 검증)."""
    from jwt.exceptions import PyJWKClientConnectionError

    from app.infra.auth import cognito as c

    class _Boom:
        def get_signing_key_from_jwt(self, token):
            raise PyJWKClientConnectionError("cannot fetch jwks")

    monkeypatch.setattr(c, "_jwk_client", lambda: _Boom())
    monkeypatch.setattr(
        c, "get_settings", lambda: SimpleNamespace(
            auth_configured=True, cognito_client_id="cid",
            cognito_issuer="iss", cognito_jwks_url="url",
        )
    )
    with pytest.raises(TokenServiceError):
        c.verify_id_token("any-token")


def test_bad_token_still_maps_to_token_error(monkeypatch):
    """kid 불일치 등 토큰 문제는 여전히 401 경로(TokenError)여야 한다."""
    import jwt as pyjwt

    from app.infra.auth import cognito as c

    class _Boom:
        def get_signing_key_from_jwt(self, token):
            raise pyjwt.exceptions.PyJWKClientError("kid not found")

    monkeypatch.setattr(c, "_jwk_client", lambda: _Boom())
    monkeypatch.setattr(
        c, "get_settings", lambda: SimpleNamespace(
            auth_configured=True, cognito_client_id="cid",
            cognito_issuer="iss", cognito_jwks_url="url",
        )
    )
    with pytest.raises(TokenError):
        c.verify_id_token("any-token")
