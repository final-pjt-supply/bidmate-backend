#!/usr/bin/env bash
#
# Private EC2 one-time preparation. It does not stop the legacy Uvicorn unit.
# The first deploy-blue-green.sh run performs the guarded port-8000 cutover.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/bootstrap-blue-green.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install --yes --no-install-recommends \
  ca-certificates \
  curl \
  docker.io \
  nginx \
  unzip

systemctl enable --now docker
systemctl enable --now nginx

install -d -m 0755 /etc/nginx/bidmate-api
install -d -m 0755 /opt/bidmate-api

if id ubuntu >/dev/null 2>&1; then
  usermod --append --groups docker ubuntu
fi

if ! command -v aws >/dev/null 2>&1; then
  case "$(uname -m)" in
    aarch64 | arm64)
      aws_arch="aarch64"
      ;;
    x86_64 | amd64)
      aws_arch="x86_64"
      ;;
    *)
      echo "Unsupported architecture: $(uname -m)" >&2
      exit 1
      ;;
  esac

  temp_dir="$(mktemp -d /tmp/bidmate-awscli.XXXXXX)"
  cleanup() {
    case "${temp_dir}" in
      /tmp/bidmate-awscli.*)
        rm -rf -- "${temp_dir}"
        ;;
    esac
  }
  trap cleanup EXIT

  curl --fail --silent --show-error --location \
    "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" \
    --output "${temp_dir}/awscliv2.zip"
  unzip -q "${temp_dir}/awscliv2.zip" -d "${temp_dir}"
  "${temp_dir}/aws/install" --update
fi

for command_name in aws curl docker flock nginx; do
  command -v "${command_name}" >/dev/null
done

nginx -t
docker --version
aws --version

cat <<'EOF'
Blue/Green runtime bootstrap complete.

No API traffic was changed. Before the first deployment:
1. Keep /home/ubuntu/bidding-agent/.env in place.
2. Ensure the EC2 role can pull bidmate-backend from ECR.
3. Set the EC2 IMDS response hop limit to 2 for bridged containers.
4. Run Backend CD manually with confirm=deploy while CD_ENABLED=false.
EOF
