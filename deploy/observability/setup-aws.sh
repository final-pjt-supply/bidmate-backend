#!/usr/bin/env bash
# AWS 자격이 있는 곳(로컬/CloudShell)에서 1회 실행.
# SNS 토픽 + 이메일 구독 + CloudWatch 알람 4종 + 월 비용 Budget 생성.
set -Eeuo pipefail

# ── 채울 값 4개 ─────────────────────────────────────────────
EMAIL="__알림받을_이메일__"
ACCOUNT_ID="__aws_계정ID__"     # aws sts get-caller-identity --query Account --output text
BUDGET_USD="50"                  # 월 비용 임계(달러)
SLOT="green"                     # 현재 활성 blue/green 슬롯
# 아래는 고정값(바꿀 일 거의 없음)
REGION="ap-northeast-2"
INSTANCE_ID="i-0e0c7b8ee9b25de06"
RDS_ID="bidmate-postgres"
# ───────────────────────────────────────────────────────────

echo "== SNS 토픽 + 이메일 구독 =="
SNS_ARN=$(aws sns create-topic --region "$REGION" --name bidmate-alerts --query TopicArn --output text)
aws sns subscribe --region "$REGION" --topic-arn "$SNS_ARN" \
  --protocol email --notification-endpoint "$EMAIL" >/dev/null
echo "SNS_ARN=$SNS_ARN  → 메일함에서 [Confirm subscription] 클릭 필요"

echo "== 알람 (a) RDS 커넥션 과다 =="
aws cloudwatch put-metric-alarm --region "$REGION" --alarm-name bidmate-rds-connections-high \
  --namespace AWS/RDS --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value="$RDS_ID" \
  --statistic Average --period 300 --threshold 40 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 --alarm-actions "$SNS_ARN"

echo "== 알람 (b) EC2 디스크(TS-23) =="
aws cloudwatch put-metric-alarm --region "$REGION" --alarm-name bidmate-disk-high \
  --namespace BidMate/Host --metric-name disk_used_percent \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --statistic Average --period 300 --threshold 80 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 --alarm-actions "$SNS_ARN"

echo "== 알람 (c) matview 신선도(TS-25, >2일) =="
aws cloudwatch put-metric-alarm --region "$REGION" --alarm-name bidmate-bidstats-stale \
  --namespace BidMate/Backend --metric-name bidmate_bid_stats_age_seconds \
  --dimensions Name=slot,Value="$SLOT" \
  --statistic Maximum --period 3600 --threshold 172800 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 --alarm-actions "$SNS_ARN"

echo "== 알람 (d) 매칭 신선도(>1시간) =="
aws cloudwatch put-metric-alarm --region "$REGION" --alarm-name bidmate-match-stale \
  --namespace BidMate/Backend --metric-name bidmate_match_results_age_seconds \
  --dimensions Name=slot,Value="$SLOT" \
  --statistic Maximum --period 600 --threshold 3600 --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 --alarm-actions "$SNS_ARN"

echo "== 비용 Budget (월 \$$BUDGET_USD, 80% 도달 시 메일) =="
aws budgets create-budget --account-id "$ACCOUNT_ID" \
  --budget "{\"BudgetName\":\"bidmate-monthly\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}" \
  --notifications-with-subscribers "[{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$EMAIL\"}]}]"

echo "== 완료: 알람 4종 + Budget 생성. SNS 구독 확인 메일 클릭했는지 체크. =="
