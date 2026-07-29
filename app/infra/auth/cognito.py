# -*- coding: utf-8 -*-
"""Cognito ID 토큰 검증.

비밀번호는 Cognito가 갖고 있고 우리는 '이 토큰이 진짜인가'만 본다. 서명을 유저풀의
공개키(JWKS)로 검증하고, 서명만 맞으면 되는 게 아니라 아래 클레임까지 확인해야 한다
— 하나라도 빠뜨리면 다른 유저풀/다른 앱의 토큰이 통과할 수 있다.

  - 서명(RS256, JWKS 공개키)
  - iss  : 우리 유저풀이 발급했는가
  - aud  : 우리 앱 클라이언트용인가
  - exp  : 만료되지 않았는가 (PyJWT가 검사)
  - token_use == "id" : 액세스 토큰이 아니라 ID 토큰인가(email 클레임이 여기 있다)

JWKS는 매 요청 받아오면 느리므로 PyJWKClient가 캐싱한다(프로세스 1회 생성).
"""
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from app.config import get_settings


class TokenError(Exception):
    """토큰이 없거나 유효하지 않음 — 호출부에서 401로 변환한다."""


@dataclass(frozen=True)
class CognitoIdentity:
    """검증된 토큰에서 뽑은 신원."""

    sub: str
    email: str | None
    name: str | None
    # 이메일 소유가 증명됐는가(가입 시 인증코드 확인 등). email로 기존 회사에 계정을
    # 잇는 경로는 이 값이 True일 때만 안전하다 — 미검증 이메일이면 남의 회사를 가로챌
    # 수 있다(계정 연결이 소유 증명 없이 일어나므로).
    email_verified: bool = False


@lru_cache
def _jwk_client() -> PyJWKClient:
    settings = get_settings()
    # PyJWKClient가 키를 캐싱한다(키 롤오버 시 자동 재조회).
    return PyJWKClient(settings.cognito_jwks_url, cache_keys=True)


def verify_id_token(token: str) -> CognitoIdentity:
    settings = get_settings()
    if not settings.auth_configured:
        raise TokenError("Cognito 설정(user pool / client id)이 비어 있습니다")

    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.cognito_client_id,  # aud 검증
            issuer=settings.cognito_issuer,       # iss 검증
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:  # 서명/만료/iss/aud 불일치 등
        raise TokenError(f"토큰 검증 실패: {exc}") from exc

    # 액세스 토큰은 aud가 없고 email도 없다 — ID 토큰만 허용한다.
    if claims.get("token_use") != "id":
        raise TokenError("ID 토큰이 아닙니다(token_use != id)")

    return CognitoIdentity(
        sub=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name"),
        email_verified=_claim_true(claims.get("email_verified")),
    )


def _claim_true(value: object) -> bool:
    """email_verified 클레임을 bool로 정규화한다.

    Cognito ID 토큰은 boolean true로 내려주지만, 문자열 "true"로 오는 경우도 방어한다.
    문자열 "false"가 truthy로 잘못 해석되지 않도록 명시적으로 비교한다.
    """
    return value is True or value == "true"
