#!/usr/bin/env bash
#
# One-time bootstrap for the private ARM64 EC2 host.
# SSM Run Command executes as root; direct SSH users can run this with sudo.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/bootstrap-container-runtime.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install --yes --no-install-recommends \
  ca-certificates \
  curl \
  docker.io \
  unzip

systemctl enable --now docker

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

docker --version
aws --version

echo "Container runtime bootstrap complete."
