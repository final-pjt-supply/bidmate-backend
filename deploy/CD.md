# Backend ECR CD

BidMate 백엔드는 PR에서 `Backend CI`를 통과한 뒤 `main`에 머지되면 다음 흐름으로 배포한다.

```text
PR Backend CI
  → main merge
  → main Backend CI 성공
  → GitHub OIDC 단기 AWS 자격증명
  → linux/arm64 이미지 빌드
  → ECR에 Git commit SHA 태그로 push
  → SSM Run Command
  → private EC2 후보 컨테이너 health check
  → 8000 포트 전환 또는 rollback
```

장기 AWS access key와 수동 ECR 토큰은 GitHub Secrets에 저장하지 않는다. GitHub Actions가
OIDC로 `bidmate-backend-github-actions-role`을 맡고, 각 실행에서 만료 시간이 짧은 ECR
로그인 토큰을 발급받는다. `AGENT_REPO_TOKEN`은 이미지 빌드 중 private
`bidmate-ai-agent` 의존성을 읽는 용도로만 BuildKit secret에 마운트한다.

## 자동 배포 게이트

운영 EC2와 `main`의 API 구성이 일치하기 전까지 저장소 변수 `CD_ENABLED=false`를 유지한다.
이 값이 `true`일 때만 `main`의 `Backend CI` 성공 이벤트가 자동 배포로 이어진다.

활성화 전 조건:

1. CI 워크플로 PR #36을 `main`에 머지한다.
2. 현재 EC2에만 포함된 회사 프로필/매칭 변경을 `main`에 모두 반영한다.
3. ECR 저장소 생성과 EC2 런타임 부트스트랩을 완료한다.
4. `workflow_dispatch`로 `main`을 한 번 수동 배포해 health check와 rollback 경로를 확인한다.
5. 저장소 변수 `CD_ENABLED`를 `true`로 바꾼다.

수동 실행도 `main` ref만 허용하며 입력값으로 정확히 `deploy`를 요구한다.

## GitHub 저장소 설정

Secret:

| 이름 | 용도 |
|---|---|
| `AGENT_REPO_TOKEN` | `bidmate-ai-agent` 저장소 Contents 읽기 |

Variables:

| 이름 | 값 |
|---|---|
| `AWS_ROLE_ARN` | `arn:aws:iam::890608337282:role/bidmate-backend-github-actions-role` |
| `AWS_REGION` | `ap-northeast-2` |
| `ECR_REPOSITORY` | `bidmate-backend` |
| `EC2_INSTANCE_ID` | `i-0e0c7b8ee9b25de06` |
| `CD_ENABLED` | 준비 중 `false`, 활성화 시 `true` |

GitHub OIDC subject는 이름 재사용 위험을 줄이기 위해 immutable 형식으로 설정했다.
AWS 역할은 다음 한 subject만 신뢰한다.

```text
repo:final-pjt-supply@296341922/bidmate-backend@1309384344:ref:refs/heads/main
```

## AWS 1회 설정

정책 원본은 [`deploy/aws`](aws/)에 둔다.

- GitHub 역할: 해당 ECR 저장소 push, 해당 EC2에 `AWS-RunShellScript` 실행, 실행 결과 조회
- EC2 역할: 해당 ECR 저장소 pull
- EC2 런타임: Docker와 ARM64 AWS CLI v2

ECR 저장소 생성 권한이 있는 AWS 관리자가 아래 작업을 한 번 수행해야 한다.

```bash
aws ecr create-repository \
  --repository-name bidmate-backend \
  --region ap-northeast-2 \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256

aws ecr put-lifecycle-policy \
  --repository-name bidmate-backend \
  --region ap-northeast-2 \
  --lifecycle-policy-text file://deploy/aws/ecr-lifecycle-policy.json
```

현재 `bid_ko` 사용자는 ECR push 권한은 있지만 `CreateRepository`와
`PutLifecyclePolicy` 권한이 없으므로 이 단계에서만 AWS 관리자 작업이 필요하다. 저장소가
생성된 뒤에는 사람이 ECR 토큰을 만들거나 전달할 일이 없다.

EC2 런타임 부트스트랩:

```bash
sudo bash deploy/bootstrap-container-runtime.sh
```

## 배포와 롤백

이미지는 `linux/arm64` 단일 플랫폼으로 빌드하고 `latest` 대신 40자리 Git commit SHA만
태그한다. 같은 커밋을 재배포할 때 ECR에 SHA 이미지가 이미 있으면 immutable 태그를
덮어쓰지 않고 기존 이미지를 재사용한다. 배포 스크립트는 다음 순서로 작동한다.

1. EC2 인스턴스 역할로 ECR 로그인 후 정확한 SHA 이미지를 pull한다.
2. 후보 컨테이너를 `127.0.0.1:18000`에 실행해 `/health`를 확인한다.
3. 후보가 정상일 때만 기존 컨테이너 또는 systemd 서비스를 정지한다.
4. 새 컨테이너를 `0.0.0.0:8000`에 실행해 다시 `/health`를 확인한다.
5. 최종 확인 실패 시 이전 이미지 또는 기존 systemd 서비스로 되돌린다.

첫 컨테이너 배포가 성공하면 기존 `bidmate-api.service`는 비활성화하되 삭제하지 않아
첫 배포 롤백에 사용할 수 있게 한다. 컨테이너 로그는 파일당 10MB, 최대 3개로 회전한다.

## 주의사항

- `.env`는 이미지에 넣지 않고 기존 `/home/ubuntu/bidding-agent/.env`를
  `--env-file`로 주입한다.
- Alembic migration은 CD에서 자동 실행하지 않는다. 현재 RDS에는 파이프라인 소유
  테이블이 함께 있으므로 schema 변경 PR에서 별도 검토·백업 후 실행한다.
- EC2 루트 디스크가 작아 7일보다 오래된 미사용 로컬 이미지는 성공 배포 뒤 정리한다.
- 자동 배포 활성화는 `main`이 현재 서버 기능을 모두 포함하는지 확인한 뒤 진행한다.
