# Backend Nginx Blue/Green CI/CD

BidFriend 백엔드는 PR에서 검증한 뒤 `main`에 머지된 Git commit SHA 이미지를 ECR에
보관하고, SSM으로 프라이빗 EC2의 비활성 슬롯에 배포한다.

```text
Pull request
  → Python/API 테스트
  → private bidmate-ai-agent import
  → linux/arm64 Docker build 검증
  → main merge
  → GitHub OIDC 단기 AWS 자격증명
  → ECR SHA 이미지
  → SSM Run Command
  → inactive Blue/Green container
  → /health + /ready + EC2 role 확인
  → Nginx 원자적 전환
  → 실제 :8000 스모크 테스트
  → 이전 슬롯 drain + graceful stop
```

장기 AWS access key는 GitHub에 저장하지 않는다. `AGENT_REPO_TOKEN`은 private
에이전트 의존성을 읽는 최소 범위 토큰이며 CI의 임시 Git header와 Docker BuildKit
secret으로만 사용한다.

## 런타임 구조

```text
frontend/VPC → 10.0.140.134:8000 Nginx
                                ├─ Blue  127.0.0.1:8001
                                └─ Green 127.0.0.1:8002
```

Nginx만 8000 포트를 소유한다. Docker 포트는 loopback에만 바인딩하므로 VPC의 다른
호스트가 8001·8002로 직접 접근할 수 없다.

## GitHub 설정

Secret:

| 이름 | 용도 |
|---|---|
| `AGENT_REPO_TOKEN` | `bidmate-ai-agent` Contents 읽기 |

Variables:

| 이름 | 값 |
|---|---|
| `AWS_ROLE_ARN` | GitHub OIDC 배포 역할 ARN |
| `AWS_REGION` | `ap-northeast-2` |
| `ECR_REPOSITORY` | `bidmate-backend` |
| `EC2_INSTANCE_ID` | `i-0e0c7b8ee9b25de06` |
| `EC2_ENV_FILE` | `/home/ubuntu/bidding-agent/.env` |
| `DRAIN_SECONDS` | `30` |
| `RUN_MIGRATIONS` | 기본 `false` |
| `CD_ENABLED` | 최초 검증 전 `false` |

`production` GitHub Environment를 만들고 필요하면 required reviewer를 설정한다. 자동
배포는 `CD_ENABLED=true`일 때만 동작한다. 비활성 상태에서도 `main`에서
`workflow_dispatch`를 실행하고 `confirm=deploy`를 입력하면 최초 배포를 검증할 수 있다.

## AWS 1회 설정

정책 원본은 `deploy/aws/`에 있다.

- GitHub 역할: ECR push, 지정 EC2 SSM 명령, 명령 결과 조회
- EC2 역할: 지정 ECR repository pull, 기존 S3·Bedrock 런타임 권한
- ECR: immutable tag, scan-on-push, 최근 이미지 30개 lifecycle
- EC2: SSM Agent, Docker, Nginx, AWS CLI

브리지 네트워크의 컨테이너가 EC2 역할을 받으려면 IMDSv2 응답 hop limit이 2여야 한다.

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id i-0e0c7b8ee9b25de06 \
  --http-endpoint enabled \
  --http-tokens required \
  --http-put-response-hop-limit 2
```

EC2에서 한 번 실행한다.

```bash
sudo bash deploy/bootstrap-blue-green.sh
```

이 스크립트는 기존 Uvicorn 서비스를 중지하지 않는다. 첫 수동 CD가 Blue를 8001에 띄워
직접 검사한 뒤에만 systemd Uvicorn을 멈추고 Nginx가 8000을 인수한다. 전환 후 스모크
테스트가 실패하면 Nginx 설정을 제거하고 기존 systemd 서비스를 다시 시작한다.

## 배포 검증과 롤백

비활성 슬롯은 다음 순서로 검증한다.

1. `/health`: FastAPI 프로세스 생존
2. `/ready`: PostgreSQL `SELECT 1`
3. boto3가 EC2 역할 자격증명을 얻는지 확인
4. Nginx 전환 후 `/version`: SHA와 슬롯 일치
5. `/bids?page=1`: 실제 DB 조회
6. `/me`: 인증 없이 401인지 확인

전환 전 실패는 활성 슬롯에 영향을 주지 않는다. 전환 후 실패는 이전 Nginx 링크와 이전
컨테이너를 복원한다. 성공 후에는 drain 시간 동안 기존 요청을 기다린 다음 Docker
`SIGTERM`과 30초 timeout으로 이전 컨테이너를 종료한다. FastAPI lifespan 종료 과정에서
S3 이벤트 버퍼가 마지막으로 flush된다.

## Alembic 안전 규칙

`RUN_MIGRATIONS=false`가 기본이다. 마이그레이션 PR을 별도로 검토하고 다음 조건을
만족한 경우에만 일시적으로 활성화한다.

- 백엔드 소유 테이블만 변경
- Blue와 Green이 동시에 사용할 수 있는 하위 호환 변경
- 컬럼/테이블 추가를 먼저 하고 삭제는 롤백 기간 후 별도 배포
- 한 배포에서 한 번만 `alembic upgrade head`

EC2의 `flock`과 GitHub Actions concurrency가 동시 배포를 차단한다. 다중 EC2로
확장하면 PostgreSQL advisory lock 같은 전역 마이그레이션 락을 추가해야 한다.

## 알려진 상태 제약

`/agent/chat` 세션은 현재 프로세스 메모리에 있다. HTTP 요청은 무중단 전환되지만 배포
전 대화 문맥은 새 슬롯으로 전달되지 않는다. 완전한 세션 연속성이 필요하면 Redis 또는
PostgreSQL로 세션 저장소를 외부화해야 한다.
