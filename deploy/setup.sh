#!/usr/bin/env bash
# BidMate API 개발 서버 세팅 (Ubuntu 24.04 arm64 / t4g). EC2에서 실행.
# 사전조건: 이 리포가 /home/ubuntu/bidding-agent 에 존재 + 리포 루트에 .env 심어둠.
#   (private 리포라 clone엔 인증 필요 — README의 '코드 올리기' 참고)
set -euo pipefail

APP_DIR=/home/ubuntu/bidding-agent
cd "$APP_DIR"

echo "== 시스템 패키지 =="
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip

echo "== 가상환경 + 의존성 =="
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r app/requirements.txt

echo "== .env 확인 =="
if [ ! -f "$APP_DIR/.env" ]; then
  echo "!! .env 없음 — deploy/env.ec2.example 참고해 $APP_DIR/.env 심고 다시 실행" >&2
  exit 1
fi

echo "== systemd 등록 =="
sudo cp deploy/bidmate-api.service /etc/systemd/system/bidmate-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now bidmate-api

echo "== 헬스체크 =="
sleep 2
curl -fsS http://127.0.0.1:8000/health && echo " <- OK"
echo "완료. 외부 확인: http://<EC2_PUBLIC_IP>:8000/health"
