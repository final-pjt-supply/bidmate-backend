# BidMate API — 개발용 EC2 배포

프라이빗 RDS(`bidmate-postgres`)에 접근 가능한 EC2(같은 VPC)에 조회 API 2개를
uvicorn/systemd로 상시 띄우는 절차. **개발용**(무인증, SG로 접근 제한).

## 서버 스펙
- t4g.small (ARM 2GB) / Ubuntu 24.04 arm64 / gp3 10GB
- 퍼블릭 서브넷 + 공인 IP, RDS와 **같은 VPC**
- SG `bidmate-api-sg`: inbound 22(내 IP), 8000(팀 IP)
- **RDS SG에 5432 ← `bidmate-api-sg` 허용 추가** (필수)

## 코드 올리기 (private 리포라 인증 필요) — 택1
- **A. scp**: 로컬에서 `git archive`로 떠서 올리기 (키만 있으면 됨, 가장 단순)
  ```
  git archive --format=tar.gz -o /tmp/app.tgz HEAD
  scp -i bidmate-api.pem /tmp/app.tgz ubuntu@<EC2_IP>:/home/ubuntu/
  ssh -i bidmate-api.pem ubuntu@<EC2_IP> \
    'mkdir -p bidding-agent && tar xzf app.tgz -C bidding-agent'
  ```
- **B. GitHub PAT/Deploy Key**: EC2에서 `git clone https://<PAT>@github.com/final-pjt-supply/bidding-agent.git`

## 배포 절차
1. 코드 올리기(위)
2. `.env` 심기: `deploy/env.ec2.example` 참고 → `/home/ubuntu/bidding-agent/.env` (비번 채우기)
3. `cd /home/ubuntu/bidding-agent && bash deploy/setup.sh`
4. 확인: `curl http://<EC2_PUBLIC_IP>:8000/health` → `{"status":"ok"}`
5. 프론트에 전달: `http://<EC2_PUBLIC_IP>:8000` + 응답 스키마

## 갱신(재배포)
코드 갱신 후 `sudo systemctl restart bidmate-api`. 로그: `journalctl -u bidmate-api -f`.

## 주의
- `bidmate`는 **공유 운영 DB** — 이 서버는 **읽기 전용**. 쓰기/마이그레이션 금지.
- 무인증 상태라 SG로만 보호됨. 8000을 `0.0.0.0/0`으로 열지 말 것.
- 밤/주말 인스턴스 stop하면 compute 절약(공인 IP는 stop 중에도 과금).
