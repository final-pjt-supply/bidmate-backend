# -*- coding: utf-8 -*-
"""companies 조회/생성.

Cognito에 가입한 사용자는 우리 DB에 자동으로 생기지 않는다. 그래서 인증된 첫 요청에
JIT(Just-In-Time)로 회사 행을 만든다 — 별도 '가입 완료' API 없이도 company_id가
확보되고, 이후 회사정보(company_* 테이블)는 이 id에 붙는다.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db.models.company import Company


class CompanyRepository:
    """조회는 모두 '활성 회원'(deleted_at IS NULL)만 본다.

    탈퇴 행을 걸러내지 않으면, 재가입 시 이메일 매칭으로 탈퇴 전 회사가 되살아난다
    (타인이 그 이메일로 가입하면 남의 데이터를 물려받는 사고로 이어진다).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, company_id: str | int) -> Company | None:
        stmt = select(Company).where(
            Company.id == int(company_id), Company.deleted_at.is_(None)
        )
        return self._session.scalars(stmt).first()

    def get_by_cognito_sub(self, cognito_sub: str) -> Company | None:
        stmt = select(Company).where(
            Company.cognito_sub == cognito_sub, Company.deleted_at.is_(None)
        )
        return self._session.scalars(stmt).first()

    def get_by_email(self, email: str) -> Company | None:
        stmt = select(Company).where(
            Company.email == email, Company.deleted_at.is_(None)
        )
        return self._session.scalars(stmt).first()

    def get_or_create(self, *, cognito_sub: str, email: str | None, name: str) -> Company:
        """cognito_sub로 찾고, 없으면 만든다(JIT).

        같은 이메일의 '활성' 행이 있으면 새로 만들지 않고 cognito_sub를 연결한다
        (팀이 수동 생성해 둔 회사에 계정을 잇는 경우). 탈퇴한 행은 조회에서 빠지므로
        재가입은 항상 새 회사로 시작한다.
        """
        company = self.get_by_cognito_sub(cognito_sub)
        if company is not None:
            return company

        if email:
            existing = self.get_by_email(email)
            if existing is not None:
                existing.cognito_sub = cognito_sub  # 기존 회사에 계정 연결
                self._session.commit()
                self._session.refresh(existing)
                return existing

        company = Company(cognito_sub=cognito_sub, email=email, name=name)
        self._session.add(company)
        self._session.commit()
        self._session.refresh(company)
        return company

    def is_withdrawn_sub(self, cognito_sub: str) -> bool:
        """이 sub로 탈퇴한 이력이 있는가.

        탈퇴 직후에도 클라이언트에는 유효한 토큰이 남아 있다. 이걸 걸러내지 않으면
        다음 요청에서 JIT 생성이 돌아 계정이 되살아난다.
        """
        stmt = select(Company.id).where(
            Company.cognito_sub == cognito_sub, Company.deleted_at.is_not(None)
        )
        return self._session.scalars(stmt).first() is not None

    def soft_delete(self, company_id: str | int) -> bool:
        """탈퇴 — 행을 지우지 않고 시각만 기록한다(30일 보관 후 파기 예정).

        cognito_sub는 그대로 둔다: 지워버리면 '이 sub는 탈퇴했다'는 판별 근거가 사라져,
        남아 있는 토큰으로 요청이 오면 JIT가 계정을 다시 만들어 버린다.
        """
        company = self.get_by_id(company_id)
        if company is None:
            return False
        company.deleted_at = datetime.now()
        self._session.commit()
        return True
