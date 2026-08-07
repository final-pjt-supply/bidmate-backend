# 관측성 세팅 (CloudWatch → Grafana) — 실행 런북

백엔드 관측을 CloudWatch로 통합하고 Grafana에서 본다. 코드(`GET /metrics`)와 이 설정
파일들은 준비돼 있고(레포), **아래 단계는 AWS 접근이 필요해 직접 실행**한다.

이 세팅으로 얻는 것:
- **로그**: 컨테이너 로그 → CloudWatch Logs `/bidmate/api`(30일 보존). 로컬 `docker logs`도 유지.
- **호스트 지표**: CPU·메모리·디스크%(TS-23 대비) → `BidMate/Host`.
- **앱 지표**: `/metrics`의 풀·matview/매칭 신선도 → `BidMate/Backend`.
- **알람 + 비용 알림 + Grafana 대시보드**.

리전은 `ap-northeast-2`, 백엔드 인스턴스 `i-0e0c7b8ee9b25de06` 기준. `<...>`는 채울 값.

---

## 0. 사전 — IAM (EC2 역할에 권한 부여)
백엔드 EC2의 인스턴스 역할에 CloudWatch 권한을 붙인다.
```bash
# 역할 이름 확인
aws ec2 describe-instances --region ap-northeast-2 --instance-ids i-0e0c7b8ee9b25de06 \
  --query "Reservations[].Instances[].IamInstanceProfile.Arn" --output text
# 위에서 나온 프로파일의 역할에 정책 부착
aws iam attach-role-policy --role-name <EC2_ROLE_NAME> \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy
```

## 1. CloudWatch Agent 설치 + 설정 배치 (백엔드 EC2에서, SSM)
```bash
# 설치 (Ubuntu arm64)
wget -q https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/arm64/latest/amazon-cloudwatch-agent.deb
sudo dpkg -i amazon-cloudwatch-agent.deb

# 레포의 설정 2개를 규정 경로에 복사 (파일을 EC2로 올린 뒤)
sudo cp cloudwatch-agent-config.json /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
sudo mkdir -p /opt/aws/amazon-cloudwatch-agent/var
sudo cp prometheus.yaml /opt/aws/amazon-cloudwatch-agent/var/prometheus.yaml

# 적용 + 시작
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s
```
> 설정 파일을 EC2로 올리는 법: `deploy/observability/`를 git으로 받거나 SSM으로 내용을 붙여넣기.
> prometheus.yaml의 `slot`은 현재 활성 슬롯(blue/green)에 맞춰 둔다.

## 2. 검증 (지표·로그가 실제로 올라오나)
```bash
sudo systemctl status amazon-cloudwatch-agent
sudo tail -n 30 /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log

# 로그 그룹 생겼나
aws logs describe-log-groups --region ap-northeast-2 --log-group-name-prefix /bidmate/api
# 앱 지표 올라오나 (수 분 후)
aws cloudwatch list-metrics --region ap-northeast-2 --namespace BidMate/Backend --output table
aws cloudwatch list-metrics --region ap-northeast-2 --namespace BidMate/Host --output table
```
`bidmate_bid_stats_age_seconds` 등이 보이면 성공.

## 3. Grafana — CloudWatch 데이터소스 연결
- Grafana → **Connections → Data sources → Add → CloudWatch**
- 인증: EC2/워크스페이스 역할 또는 액세스 키(권한: `cloudwatch:GetMetricData`·`ListMetrics`·`logs:*`(읽기))
- Default region: `ap-northeast-2`
- 패널 예:
  - `BidMate/Backend` / `bidmate_bid_stats_age_seconds` (matview 신선도)
  - `BidMate/Backend` / `bidmate_match_results_age_seconds` (매칭 신선도)
  - `BidMate/Backend` / `bidmate_db_pool_checked_out` (풀 사용량)
  - `BidMate/Host` / `disk_used_percent`, `mem_used_percent`, `cpu_usage_active`
  - `AWS/RDS` / `DatabaseConnections`, `CPUUtilization`

## 4. 알림 대상 — SNS 토픽
```bash
aws sns create-topic --region ap-northeast-2 --name bidmate-alerts
# 위 출력 TopicArn을 아래에 사용
aws sns subscribe --region ap-northeast-2 --topic-arn <SNS_ARN> \
  --protocol email --notification-endpoint <YOUR_EMAIL>
# 메일함에서 구독 확인 클릭
```

## 5. CloudWatch 알람
```bash
SNS=<SNS_ARN>

# (a) RDS 커넥션 과다 — ADR-31 캐스케이드 조기감지
aws cloudwatch put-metric-alarm --region ap-northeast-2 --alarm-name bidmate-rds-connections-high \
  --namespace AWS/RDS --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=bidmate-postgres \
  --statistic Average --period 300 --threshold 40 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 --alarm-actions "$SNS"

# (b) EC2 디스크 — TS-23(디스크 풀로 CD 실패) 재발 방지
aws cloudwatch put-metric-alarm --region ap-northeast-2 --alarm-name bidmate-disk-high \
  --namespace BidMate/Host --metric-name disk_used_percent \
  --dimensions Name=InstanceId,Value=i-0e0c7b8ee9b25de06 \
  --statistic Average --period 300 --threshold 80 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 --alarm-actions "$SNS"

# (c) matview 신선도 — 외부 Airflow DAG 정지 감지(TS-25). 2일(172800s) 초과면 알람
aws cloudwatch put-metric-alarm --region ap-northeast-2 --alarm-name bidmate-bidstats-stale \
  --namespace BidMate/Backend --metric-name bidmate_bid_stats_age_seconds \
  --dimensions Name=slot,Value=green \
  --statistic Maximum --period 3600 --threshold 172800 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 --alarm-actions "$SNS"

# (d) 매칭 신선도 — 주기 갱신(#80) 멈춤 감지. 1시간(3600s) 초과면 알람
aws cloudwatch put-metric-alarm --region ap-northeast-2 --alarm-name bidmate-match-stale \
  --namespace BidMate/Backend --metric-name bidmate_match_results_age_seconds \
  --dimensions Name=slot,Value=green \
  --statistic Maximum --period 600 --threshold 3600 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 --alarm-actions "$SNS"
```

## 6. AWS Budgets — 비용 알림 (임계금액은 정할 것, 예: 월 $50)
```bash
aws budgets create-budget --account-id <ACCT_ID> \
  --budget '{"BudgetName":"bidmate-monthly","BudgetLimit":{"Amount":"50","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"<YOUR_EMAIL>"}]}]'
```

---

## 채울 값 요약
| 자리 | 값 |
|---|---|
| `<EC2_ROLE_NAME>` | 백엔드 인스턴스 프로파일의 역할명(0단계에서 확인) |
| `<SNS_ARN>` | 4단계 create-topic 출력 |
| `<YOUR_EMAIL>` | 알림 받을 이메일 |
| `<ACCT_ID>` | AWS 계정 ID (`aws sts get-caller-identity`) |

## 주의
- `/metrics`는 미인증 — 백엔드가 프라이빗 VPC라 내부에서만 도달. 공개 노출 시 nginx에서 내부망만 허용.
- prometheus.yaml의 `slot` 라벨은 활성 슬롯과 맞춘다(전환 시 갱신). 슬롯 무관하게 보려면 라벨을 빼고 dimension에서 제외.
- CloudWatch Agent 버전에 따라 prometheus/emf 스키마가 다를 수 있음 — 2단계 agent 로그로 확인.
