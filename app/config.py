# -*- coding: utf-8 -*-
"""API 서버 설정. 환경변수만 읽는다(코드에 접속정보 하드코딩 금지).

로컬(db/docker-compose.yml)은 POSTGRES_* 기본값과 맞춰 무설정으로 뜨고,
배포(Lambda)는 환경변수/Secrets Manager로 주입한다. 파이프라인 배치가 쓰는
MERGE_DB_* 와는 이름을 분리한다 — 같은 RDS를 가리키더라도 API 서버와 배치는
독립 배포 단위라 설정 통로를 섞지 않는다.
"""
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env(리포 루트, gitignore됨)에서 로컬/터널 접속정보를 읽는다. 배포(Lambda)는
    # 환경변수/Secrets Manager가 .env보다 우선한다(env가 파일값을 덮어씀).
    model_config = SettingsConfigDict(
        env_prefix="", extra="ignore", env_file=".env", env_file_encoding="utf-8"
    )

    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="bidding_agent")
    postgres_user: str = Field(default="bidding_agent")
    postgres_password: str = Field(default="bidding_agent")
    # RDS는 보통 SSL 필수(sslmode=require). private RDS를 SSH 터널로 붙을 때는
    # 터널 자체가 암호화되지만 libpq SSL도 켜두는 게 안전(RDS 파라미터가 강제 가능).
    # 로컬 docker(무SSL)에선 None으로 둔다.
    postgres_sslmode: str | None = Field(default=None)

    # SQLAlchemy 커넥션 풀. Lambda(stateless, 컨테이너당 요청 1개)에선 큰 풀이
    # 무의미하고 RDS 커넥션만 소진하므로 작게 잡는다. EC2 상시 서버로 띄울 땐
    # .env에서 db_pool_size를 5 내외로 올린다(공유 운영 DB라 과하게는 금지).
    db_pool_size: int = Field(default=1)
    db_max_overflow: int = Field(default=2)

    # 배포 식별자. Docker 이미지는 Git commit SHA를 APP_VERSION으로 주입하고,
    # Blue/Green 슬롯은 DEPLOYMENT_SLOT으로 주입한다. 로컬은 dev/local.
    app_version: str = Field(default="dev")
    deployment_slot: str = Field(default="local")

    # CORS 허용 오리진. 브라우저(프론트)가 다른 오리진에서 API를 부르면 이 목록에
    # 없는 한 브라우저가 차단한다. 콤마 구분 문자열로 두는 이유: list 타입은
    # pydantic-settings가 env 값을 JSON으로 파싱하려 해 "a,b" 같은 평범한 입력이
    # 깨진다. 기본값은 로컬 프론트 개발 포트(Vite 5173 / CRA·Next 3000).
    cors_origins: str = Field(default="http://localhost:5173,http://localhost:3000")

    # 고객 여정 이벤트 S3 적재. 분석을 운영 DB에서 분리(NDJSON, 날짜 파티션).
    # 배칭: flush_max건 또는 flush_interval초마다 S3에 한 파일로 올림(best-effort).
    s3_bucket: str = Field(default="")               # 예: bidmate
    s3_events_prefix: str = Field(default="user_events")
    aws_region: str = Field(default="")              # 비우면 boto3 기본(EC2 리전)
    event_flush_max: int = Field(default=20)
    # 2초: 테스트에서 클릭 후 거의 즉시 S3에 뜨게(체감 실시간). 몰릴 땐 여전히
    # flush_max(20건)로 묶여 tiny-file을 어느 정도 방지. 진짜 실시간은 Firehose.
    event_flush_interval_sec: float = Field(default=2.0)

    # --- 인증(AWS Cognito User Pool) ---
    # 비밀번호는 Cognito가 보관한다(우리 DB에 저장하지 않음). 서버는 토큰만 검증하고
    # cognito_sub로 companies 행을 찾는다. 세 값 모두 비밀이 아니라 .env로 충분.
    cognito_region: str = Field(default="ap-northeast-2")
    cognito_user_pool_id: str = Field(default="")
    cognito_client_id: str = Field(default="")

    # 로컬 개발 편의: 토큰 없이 고정 회사로 통과시킨다.
    # ⚠ 운영에는 절대 true로 두지 말 것(인증이 통째로 무력화된다).
    auth_disabled: bool = Field(default=False)
    dev_company_id: str = Field(default="")

    # --- 개인화 추천(제목 임베딩) ---
    # 제목 벡터는 입찰 ETL이 bid_chunks 인덱스에 type=title로 적재한다. API는 새 벡터를
    # 저장하지 않고 회사 관심 쿼리만 임베딩한 뒤, 자격 후보 bid_id를 knn filter로 건다.
    opensearch_url: str = Field(
        default="https://localhost:9243",
        validation_alias=AliasChoices(
            "OPENSEARCH_URL",
            "OPENSEARCH_LOCAL_URL",
            "OPENSEARCH_ENDPOINT",
        ),
    )
    opensearch_user: str = Field(default="")
    opensearch_password: str = Field(default="")
    opensearch_index_name: str = Field(
        default="bid_chunks",
        validation_alias=AliasChoices(
            "OPENSEARCH_INDEX_NAME",
            "OPENSEARCH_INDEX",
        ),
    )
    opensearch_verify_certs: bool = Field(default=True)
    cf_account_id: str = Field(default="")
    cf_api_token: str = Field(default="")
    cf_embedding_model: str = Field(default="@cf/baai/bge-m3")

    @property
    def cognito_issuer(self) -> str:
        """토큰의 iss 클레임과 대조할 발급자 URL."""
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com/"
            f"{self.cognito_user_pool_id}"
        )

    @property
    def cognito_jwks_url(self) -> str:
        """서명 검증용 공개키 목록(JWKS). PyJWKClient가 캐싱한다."""
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def auth_configured(self) -> bool:
        return bool(self.cognito_user_pool_id and self.cognito_client_id)

    @property
    def cors_origins_list(self) -> list[str]:
        """콤마 구분 문자열 → 리스트. 공백/빈 항목은 버린다."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        # RDS 자격증명엔 특수문자(@:/ 등)가 흔해 URL-encode 하지 않으면 파싱이 깨진다.
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        url = (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
        if self.postgres_sslmode:
            url += f"?sslmode={self.postgres_sslmode}"
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
