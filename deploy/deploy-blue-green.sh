#!/usr/bin/env bash
#
# Deploy one immutable ECR image into the inactive slot and atomically switch
# Nginx after direct health/readiness checks. SSM Run Command executes as root.
set -Eeuo pipefail

readonly LEGACY_UNIT="bidmate-api"
readonly BLUE_CONTAINER="bidmate-api-blue"
readonly GREEN_CONTAINER="bidmate-api-green"
readonly BLUE_PORT="8001"
readonly GREEN_PORT="8002"
readonly DEPLOY_ROOT="/opt/bidmate-api"
readonly NGINX_ROOT="/etc/nginx/bidmate-api"
readonly ACTIVE_LINK="/etc/nginx/conf.d/bidmate-api.conf"
readonly STATE_FILE="${DEPLOY_ROOT}/active-slot"
readonly LOCK_FILE="/var/lock/bidmate-api-deploy.lock"

image_uri="${1:-}"
aws_region="${2:-}"
env_file="${3:-/home/ubuntu/bidding-agent/.env}"
blue_source="${4:-}"
green_source="${5:-}"
run_migrations="${6:-false}"
drain_seconds="${7:-30}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ ! "${image_uri}" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^:]+:[0-9a-f]{40}$ ]]; then
  echo "Image must be a private ECR URI tagged with a full Git commit SHA." >&2
  exit 1
fi

if [[ -z "${aws_region}" || ! -f "${env_file}" ]]; then
  echo "AWS region and an existing environment file are required." >&2
  exit 1
fi

if [[ ! -f "${blue_source}" || ! -f "${green_source}" ]]; then
  echo "Blue and Green Nginx configuration files are required." >&2
  exit 1
fi

if [[ "${run_migrations}" != "true" && "${run_migrations}" != "false" ]]; then
  echo "run_migrations must be true or false." >&2
  exit 1
fi

if [[ ! "${drain_seconds}" =~ ^[0-9]+$ ]]; then
  echo "drain_seconds must be an integer." >&2
  exit 1
fi

for command_name in aws curl docker flock install nginx systemctl timeout; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 1
  fi
done

install -d -m 0755 "${DEPLOY_ROOT}" "${NGINX_ROOT}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another backend deployment is already running." >&2
  exit 1
fi

version="${image_uri##*:}"
registry="${image_uri%%/*}"

active_slot=""
# readlink -f prints the canonical path even when ACTIVE_LINK does not exist,
# which would break first-run detection (a clean box has no link yet). Only
# resolve when it is a real symlink; otherwise treat it as absent.
if [[ -L "${ACTIVE_LINK}" ]]; then
  linked_conf="$(readlink -f "${ACTIVE_LINK}" 2>/dev/null || true)"
else
  linked_conf=""
fi
case "${linked_conf}" in
  "${NGINX_ROOT}/blue.conf")
    active_slot="blue"
    ;;
  "${NGINX_ROOT}/green.conf")
    active_slot="green"
    ;;
  "")
    if [[ -f "${STATE_FILE}" ]]; then
      echo "Active-slot state exists without an Nginx link; manual recovery is required." >&2
      exit 1
    fi
    ;;
  *)
    echo "Unexpected Nginx active target: ${linked_conf}" >&2
    exit 1
    ;;
esac

# The atomic Nginx link is the traffic source of truth. Repair a stale state
# file after a host reboot that occurred between switch and state-file update.
if [[ -n "${active_slot}" ]]; then
  recorded_slot="$(tr -d '[:space:]' < "${STATE_FILE}" 2>/dev/null || true)"
  if [[ "${recorded_slot}" != "${active_slot}" ]]; then
    printf '%s\n' "${active_slot}" > "${STATE_FILE}.recovered"
    mv -Tf "${STATE_FILE}.recovered" "${STATE_FILE}"
  fi
fi

if [[ "${active_slot}" == "blue" ]]; then
  candidate_slot="green"
  candidate_container="${GREEN_CONTAINER}"
  candidate_port="${GREEN_PORT}"
  old_container="${BLUE_CONTAINER}"
elif [[ "${active_slot}" == "green" ]]; then
  candidate_slot="blue"
  candidate_container="${BLUE_CONTAINER}"
  candidate_port="${BLUE_PORT}"
  old_container="${GREEN_CONTAINER}"
else
  # First container deployment: keep the legacy unit until Blue is ready.
  candidate_slot="blue"
  candidate_container="${BLUE_CONTAINER}"
  candidate_port="${BLUE_PORT}"
  old_container=""
fi

candidate_conf="${NGINX_ROOT}/${candidate_slot}.conf"
# Same guard as above: only resolve a genuine symlink so first-run rollback
# releases port 8000 for Uvicorn instead of restoring a non-existent link.
if [[ -L "${ACTIVE_LINK}" ]]; then
  previous_link="$(readlink -f "${ACTIVE_LINK}" 2>/dev/null || true)"
else
  previous_link=""
fi
legacy_was_active="false"
legacy_stopped="false"
switched="false"
link_changed="false"

if systemctl is-active --quiet "${LEGACY_UNIT}"; then
  legacy_was_active="true"
fi

if [[ -n "${active_slot}" ]]; then
  if [[ "$(docker inspect --format '{{.State.Running}}' "${old_container}" 2>/dev/null || true)" != "true" ]]; then
    echo "Active ${active_slot} container is not running; refusing an unsafe switch." >&2
    exit 1
  fi
fi

rollback() {
  local exit_code="$?"
  trap - ERR
  set +e

  echo "Deployment failed; restoring the previous traffic target." >&2
  docker logs --tail 200 "${candidate_container}" >&2 || true

  if [[ "${link_changed}" == "true" ||
        "${switched}" == "true" ||
        "${legacy_stopped}" == "true" ]]; then
    if [[ -n "${active_slot}" ]]; then
      docker start "${old_container}" >/dev/null 2>&1 || true
    fi

    if [[ -n "${previous_link}" ]]; then
      ln -sfn "${previous_link}" "${ACTIVE_LINK}.rollback"
      mv -Tf "${ACTIVE_LINK}.rollback" "${ACTIVE_LINK}"
    else
      rm -f "${ACTIVE_LINK}"
    fi

    if nginx -t; then
      if [[ -z "${previous_link}" && "${legacy_was_active}" == "true" ]]; then
        # First-cutover rollback: release port 8000 before restarting Uvicorn.
        systemctl stop nginx || true
      else
        systemctl reload nginx || true
      fi
    fi

    if [[ "${legacy_was_active}" == "true" ]]; then
      systemctl enable "${LEGACY_UNIT}" >/dev/null 2>&1 || true
      systemctl restart "${LEGACY_UNIT}" || true
    fi

    if [[ -n "${active_slot}" ]]; then
      printf '%s\n' "${active_slot}" > "${STATE_FILE}.rollback"
      mv -Tf "${STATE_FILE}.rollback" "${STATE_FILE}"
    else
      rm -f "${STATE_FILE}" "${STATE_FILE}.next"
    fi
  fi

  docker rm --force "${candidate_container}" >/dev/null 2>&1 || true
  docker logout "${registry}" >/dev/null 2>&1 || true
  exit "${exit_code}"
}
trap rollback ERR

wait_for_endpoint() {
  local url="$1"
  local _

  for _ in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 4 "${url}" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

# nginx graceful reload keeps old workers accepting new connections during the
# drain window, so /version can briefly still report the old slot. Poll until
# the candidate slot answers instead of failing on the first (racy) read.
wait_for_version() {
  local expect_slot="$1" expect_version="$2" payload _
  for _ in $(seq 1 15); do
    payload="$(curl --fail --silent --max-time 5 \
      "http://127.0.0.1:8000/version" 2>/dev/null || true)"
    if [[ "${payload}" == *"\"version\":\"${expect_version}\""* &&
          "${payload}" == *"\"slot\":\"${expect_slot}\""* ]]; then
      return 0
    fi
    sleep 2
  done
  echo "Nginx did not switch to ${expect_slot}: ${payload}" >&2
  return 1
}

echo "Authenticating the EC2 role to ${registry}."
aws ecr get-login-password --region "${aws_region}" \
  | docker login --username AWS --password-stdin "${registry}" >/dev/null

echo "Pulling immutable image ${image_uri}."
docker pull "${image_uri}"

install -m 0644 "${blue_source}" "${NGINX_ROOT}/blue.conf"
install -m 0644 "${green_source}" "${NGINX_ROOT}/green.conf"

# ⚠ 마이그레이션 제약(QA #9): 이 upgrade는 후보 검증 '전에' 공유 prod RDS에 실행되고
# (옛 슬롯이 아직 서빙 중), 배포 실패 시 rollback은 스키마를 되돌리지 않는다. 따라서
# 반드시 '추가/하위호환(expand→migrate→contract)' 마이그레이션만 허용한다 — 옛 슬롯이
# 새 스키마로도 계속 동작해야 하고, 파괴적 변경(컬럼 DROP 등)은 전환 창에서 옛 슬롯을
# 깨뜨린다. RUN_MIGRATIONS는 opt-in(기본 off)이므로 켤 때 리뷰로 이 규약을 강제할 것.
if [[ "${run_migrations}" == "true" ]]; then
  echo "Running the reviewed backward-compatible Alembic migration."
  docker run --rm \
    --env-file "${env_file}" \
    --env "AWS_REGION=${aws_region}" \
    --env "AWS_DEFAULT_REGION=${aws_region}" \
    --env "APP_VERSION=${version}" \
    "${image_uri}" \
    alembic upgrade head
fi

docker rm --force "${candidate_container}" >/dev/null 2>&1 || true

docker run --detach \
  --name "${candidate_container}" \
  --env-file "${env_file}" \
  --env "AWS_REGION=${aws_region}" \
  --env "AWS_DEFAULT_REGION=${aws_region}" \
  --env "APP_VERSION=${version}" \
  --env "DEPLOYMENT_SLOT=${candidate_slot}" \
  --publish "127.0.0.1:${candidate_port}:8000" \
  --restart unless-stopped \
  --stop-timeout 30 \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  --label com.bidmate.service=api \
  --label "com.bidmate.slot=${candidate_slot}" \
  --label "com.bidmate.version=${version}" \
  "${image_uri}" >/dev/null

wait_for_endpoint "http://127.0.0.1:${candidate_port}/health"
wait_for_endpoint "http://127.0.0.1:${candidate_port}/ready"

# Ensure bridged containers can obtain the EC2 role used by S3 and Bedrock.
timeout 15 docker exec "${candidate_container}" python -c \
  "import boto3; c=boto3.Session().get_credentials(); assert c is not None; c.get_frozen_credentials()"

ln -sfn "${candidate_conf}" "${ACTIVE_LINK}.next"
mv -Tf "${ACTIVE_LINK}.next" "${ACTIVE_LINK}"
link_changed="true"
nginx -t

if [[ "${legacy_was_active}" == "true" ]]; then
  systemctl stop "${LEGACY_UNIT}"
  legacy_stopped="true"
fi

systemctl enable --now nginx
systemctl reload nginx
switched="true"

wait_for_endpoint "http://127.0.0.1:8000/ready"

# Drain-tolerant: retry until the candidate slot serves through :8000 (a single
# check right after reload races the old workers still draining).
wait_for_version "${candidate_slot}" "${version}"

curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:8000/bids?page=1" >/dev/null

auth_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 \
    "http://127.0.0.1:8000/me"
)"
if [[ "${auth_status}" != "401" ]]; then
  echo "Authentication smoke test expected 401, got ${auth_status}." >&2
  false
fi

printf '%s\n' "${candidate_slot}" > "${STATE_FILE}.next"
mv -Tf "${STATE_FILE}.next" "${STATE_FILE}"

if [[ "${legacy_was_active}" == "true" ]]; then
  systemctl disable "${LEGACY_UNIT}" >/dev/null 2>&1 || true
fi

if [[ -n "${old_container}" ]]; then
  echo "Draining ${active_slot} for ${drain_seconds}s."
  sleep "${drain_seconds}"
  docker stop --time 30 "${old_container}" >/dev/null
fi

trap - ERR
# --all: SHA로 태그된 옛 배포 이미지까지 정리한다. dangling(--force만)으로는 태그된
# 미사용 이미지가 안 지워져 매 배포마다 쌓여 8.7GB 디스크가 결국 가득 찬다(no space
# left on device로 배포 실패). 실행 중 컨테이너(활성 슬롯·드레인 후 정지된 이전 슬롯)가
# 참조하는 이미지는 보존되고, 그 외 미사용만 제거된다. 롤백은 ECR 재풀로 가능.
docker image prune --all --force >/dev/null
docker logout "${registry}" >/dev/null 2>&1 || true

echo "Deployment succeeded: ${candidate_slot} serves ${version}."
