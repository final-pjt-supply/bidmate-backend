# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY app/requirements.txt ./requirements.txt

# private bidmate-ai-agent 토큰은 이 RUN에서만 BuildKit secret으로 보인다.
# 환경 기반 Git 설정을 써서 builder의 ~/.gitconfig에도 토큰을 남기지 않는다.
RUN --mount=type=secret,id=agent_repo_token,required=true \
    set -eu; \
    agent_repo_token="$(cat /run/secrets/agent_repo_token)"; \
    auth_header="$(printf 'x-access-token:%s' "${agent_repo_token}" | base64 -w 0)"; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install --upgrade pip; \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0=http.https://github.com/.extraheader \
    GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${auth_header}" \
    /opt/venv/bin/pip install --requirement requirements.txt


FROM python:3.12-slim-bookworm AS runtime

ARG APP_VERSION=dev

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_VERSION="${APP_VERSION}"

RUN groupadd --system --gid 10001 bidmate \
    && useradd --system --uid 10001 --gid bidmate --home-dir /app bidmate

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=bidmate:bidmate app ./app
COPY --chown=bidmate:bidmate alembic ./alembic
COPY --chown=bidmate:bidmate alembic.ini ./alembic.ini

USER bidmate

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--timeout-graceful-shutdown=30"]
