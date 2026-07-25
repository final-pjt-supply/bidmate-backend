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

# bidmate-agents is a private Git dependency. BuildKit mounts the PAT only for
# this RUN instruction, so the token is not copied into an image layer.
RUN --mount=type=secret,id=agent_repo_token,required=true \
    set -eu; \
    agent_repo_token="$(cat /run/secrets/agent_repo_token)"; \
    auth_header="$(printf 'x-access-token:%s' "${agent_repo_token}" | base64 -w 0)"; \
    git config --global \
        http.https://github.com/.extraheader \
        "AUTHORIZATION: basic ${auth_header}"; \
    python -m venv /opt/venv; \
    /opt/venv/bin/pip install --upgrade pip; \
    /opt/venv/bin/pip install --requirement requirements.txt; \
    git config --global --unset-all \
        http.https://github.com/.extraheader


FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system --gid 10001 bidmate \
    && useradd --system --uid 10001 --gid bidmate --home-dir /app bidmate

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=bidmate:bidmate app ./app
COPY --chown=bidmate:bidmate alembic ./alembic
COPY --chown=bidmate:bidmate alembic.ini ./alembic.ini

USER bidmate

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
