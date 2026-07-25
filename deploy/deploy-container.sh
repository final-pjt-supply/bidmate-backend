#!/usr/bin/env bash
#
# Pull one immutable ECR image and replace the BidMate API container.
# The first successful container deployment disables the legacy systemd unit.
set -euo pipefail

readonly CONTAINER_NAME="bidmate-api"
readonly CANDIDATE_NAME="bidmate-api-candidate"
readonly APP_PORT="8000"
readonly CANDIDATE_PORT="18000"
readonly DEFAULT_ENV_FILE="/home/ubuntu/bidding-agent/.env"

image_uri="${1:-}"
aws_region="${2:-}"
env_file="${3:-${DEFAULT_ENV_FILE}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ -z "${image_uri}" || -z "${aws_region}" ]]; then
  echo "Usage: $0 <ecr-image-uri> <aws-region> [env-file]" >&2
  exit 1
fi

if [[ ! "${image_uri}" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^:]+:[0-9a-f]{40}$ ]]; then
  echo "Image must be a private ECR URI tagged with a full Git commit SHA." >&2
  exit 1
fi

if [[ ! -f "${env_file}" ]]; then
  echo "Environment file not found: ${env_file}" >&2
  exit 1
fi

for command_name in aws curl docker systemctl; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 1
  fi
done

registry="${image_uri%%/*}"
old_image=""
legacy_was_active="false"

health_check() {
  local port="$1"
  local _

  for _ in $(seq 1 30); do
    if curl --fail --silent --show-error \
      "http://127.0.0.1:${port}/health" >/dev/null; then
      return 0
    fi
    sleep 2
  done

  return 1
}

run_api_container() {
  local container_name="$1"
  local container_image="$2"
  local host_binding="$3"
  local restart_policy="$4"

  docker run --detach \
    --name "${container_name}" \
    --env-file "${env_file}" \
    --env "AWS_REGION=${aws_region}" \
    --env "AWS_DEFAULT_REGION=${aws_region}" \
    --publish "${host_binding}:8000" \
    --restart "${restart_policy}" \
    --stop-timeout 30 \
    --log-opt max-size=10m \
    --log-opt max-file=3 \
    --label com.bidmate.service=api \
    "${container_image}" >/dev/null
}

rollback() {
  echo "New container failed its health check; rolling back." >&2
  docker logs --tail 200 "${CONTAINER_NAME}" >&2 || true
  docker rm --force "${CONTAINER_NAME}" >/dev/null 2>&1 || true

  if [[ -n "${old_image}" ]]; then
    run_api_container \
      "${CONTAINER_NAME}" \
      "${old_image}" \
      "0.0.0.0:${APP_PORT}" \
      "unless-stopped"
    if health_check "${APP_PORT}"; then
      echo "Rollback to ${old_image} completed." >&2
    else
      echo "Rollback container also failed health checks." >&2
    fi
  elif [[ "${legacy_was_active}" == "true" ]]; then
    systemctl enable bidmate-api >/dev/null 2>&1 || true
    systemctl restart bidmate-api
    echo "Legacy bidmate-api systemd service restarted." >&2
  fi
}

echo "Authenticating the EC2 instance role to ${registry}."
aws ecr get-login-password --region "${aws_region}" \
  | docker login --username AWS --password-stdin "${registry}" >/dev/null

echo "Pulling immutable image ${image_uri}."
docker pull "${image_uri}"

if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  old_image="$(docker container inspect \
    --format '{{.Config.Image}}' "${CONTAINER_NAME}")"
fi

if systemctl is-active --quiet bidmate-api; then
  legacy_was_active="true"
fi

# Validate startup before taking port 8000 away from the current service.
docker rm --force "${CANDIDATE_NAME}" >/dev/null 2>&1 || true
run_api_container \
  "${CANDIDATE_NAME}" \
  "${image_uri}" \
  "127.0.0.1:${CANDIDATE_PORT}" \
  "no"

if ! health_check "${CANDIDATE_PORT}"; then
  echo "Candidate container failed its health check." >&2
  docker logs --tail 200 "${CANDIDATE_NAME}" >&2 || true
  docker rm --force "${CANDIDATE_NAME}" >/dev/null 2>&1 || true
  exit 1
fi

docker rm --force "${CANDIDATE_NAME}" >/dev/null

if [[ -n "${old_image}" ]]; then
  docker rm --force "${CONTAINER_NAME}" >/dev/null
fi
if [[ "${legacy_was_active}" == "true" ]]; then
  systemctl stop bidmate-api
fi

if ! run_api_container \
  "${CONTAINER_NAME}" \
  "${image_uri}" \
  "0.0.0.0:${APP_PORT}" \
  "unless-stopped"; then
  rollback
  exit 1
fi

if ! health_check "${APP_PORT}"; then
  rollback
  exit 1
fi

# Docker now owns port 8000. Keep the unit installed for first-deploy rollback,
# but prevent it from racing the container after a host reboot.
systemctl disable bidmate-api >/dev/null 2>&1 || true

# The root disk is small. Keep running images and images used in the last week.
docker image prune --all --force --filter "until=168h" >/dev/null

echo "Deployment succeeded: ${image_uri}"
