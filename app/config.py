# -*- coding: utf-8 -*-
"""API 서버 설정. 환경변수만 읽는다(코드에 접속정보 하드코딩 금지).

로컬(db/docker-compose.yml)은 POSTGRES_* 기본값과 맞춰 무설정으로 뜨고,
배포(Lambda)는 환경변수/Secrets Manager로 주입한다. 파이프라인 배치가 쓰는
MERGE_DB_* 와는 이름을 분리한다 — 같은 RDS를 가리키더라도 API 서버와 배치는
독립 배포 단위라 설정 통로를 섞지 않는다.
"""
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 인증을 꺼도 되는(개발용) 배포 슬롯. 그 외 슬롯(blue/green 등 운영)에서 auth_disabled를
# 켜면 서버가 아예 뜨지 않는다 — 조용히 무인증 개방되는 것보다 배포가 시끄럽게 실패하는
# 편이 낫다(fail-fast).
_AUTH_OPTIONAL_SLOTS = {"local", "dev"}


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

    # --- 챗 세션 길이 상한 ---
    # 한 대화가 무한정 길어지는 것을 막는 소프트캡. 초과하면 LLM을 부르지 않고
    # "새 대화를 시작하라"고 안내한다(문맥 비용·무한 사용 방지). 에이전트가 요약을
    # 들고 다녀 토큰이 폭증하진 않으므로 하드 차단이 아닌 가드레일 성격.
    session_max_turns: int = Field(default=20)

    # --- 챗 레이트리밋(회사 company_id 기준, Bedrock 비용 방어) ---
    # 분당·동시성은 인메모리(단일 프로세스라 정확). 일일은 지속 저장이 필요해 RDS.
    # 다중 인스턴스/Lambda 전환 시 Redis로 이관. 검증실패(422)는 LLM 미도달이라 미포함,
    # 에이전트 실패(502)는 LLM이 돌았으므로 포함.
    rate_limit_per_min: int = Field(default=10)      # 회사당 분당 요청
    chat_concurrency_max: int = Field(default=3)     # 회사당 동시 진행 요청
    chat_daily_max: int = Field(default=500)         # 회사당 하루 요청

    @model_validator(mode="after")
    def _validate_production_config(self) -> "Settings":
        """운영 슬롯에서 '조용히 깨진 채' 뜨는 설정을 기동 시점에 막는다(fail-fast).

        설정을 읽는 시점(get_settings)에 돌아, 조건이 나쁘면 예외가 나 서버가 기동조차
        못 한다 → Blue/Green 배포에서 새 슬롯이 헬스체크에 실패해 자동 롤백된다. 스모크
        (무토큰 401)로는 안 잡히는 미스컨피그(무인증 개방·로그인 503·CORS 전면 차단)를
        조용히 트래픽에 노출하지 않고 배포가 시끄럽게 실패하게 한다.
        """
        if not self.is_production_slot:
            return self

        # ① 인증을 끈 채 운영에 뜨는 것 금지(조용한 무인증 개방 방지).
        if self.auth_disabled:
            raise ValueError(
                f"AUTH_DISABLED=true는 {sorted(_AUTH_OPTIONAL_SLOTS)} 슬롯에서만 허용된다 "
                f"(현재 deployment_slot={self.deployment_slot!r}). 운영에서는 인증을 끌 수 없다."
            )
        # ② Cognito 미설정이면 로그인 토큰이 503으로 죽는데 무토큰 401 스모크는 통과해
        #    깨진 배포가 트래픽을 받는다 — 부팅부터 막는다(QA M1).
        if not self.auth_configured:
            raise ValueError(
                "운영 슬롯은 COGNITO_USER_POOL_ID·COGNITO_CLIENT_ID가 필요하다 "
                f"(deployment_slot={self.deployment_slot!r}). 인증 미설정으론 기동하지 않는다."
            )
        # ③ CORS가 localhost뿐이면 프론트 브라우저 호출이 전부 막힌다 — 실 오리진 강제(QA M2).
        if not self._has_non_localhost_cors():
            raise ValueError(
                "운영 슬롯은 CORS_ORIGINS에 실제 프론트 오리진이 필요하다 "
                f"(현재={self.cors_origins!r}). localhost만으로는 브라우저 호출이 막힌다."
            )
        return self

    def _has_non_localhost_cors(self) -> bool:
        """localhost/127.0.0.1이 아닌 실제 오리진이 하나라도 있는가."""
        return any(
            not (o.startswith("http://localhost") or o.startswith("http://127.0.0.1"))
            for o in self.cors_origins_list
        )

    # --- 매칭 주기 갱신(#80) ---
    # 신규 공고를 match_results에 반영하는 내장 스케줄러. 기본 off — 로컬/테스트가
    # 운영 RDS에 붙은 채 배치를 돌리는 사고를 막는다. 실배포에서만 .env로 켠다.
    match_refresh_enabled: bool = Field(default=False)
    # 수집·정규화가 5분 주기라 그보다 촘촘하게 돌 이유가 없다.
    match_refresh_interval_sec: int = Field(default=300)
    # 전체 재계산(마감 지난 행 정리)을 도는 KST 시각. 트래픽이 가장 적은 새벽.
    match_refresh_full_hour: int = Field(default=4)

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
    def is_production_slot(self) -> bool:
        """운영 슬롯인가(local/dev가 아닌 모든 슬롯). 인증·CORS 기동 가드의 기준."""
        return self.deployment_slot.lower() not in _AUTH_OPTIONAL_SLOTS

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
