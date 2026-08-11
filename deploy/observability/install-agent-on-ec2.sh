#!/usr/bin/env bash
# 백엔드 EC2에서 실행(SSM 셸에 통째로 붙여넣기 1회).
# CloudWatch Agent 설치 + 설정(로그+호스트지표+/metrics 스크레이프) 적용 + 시작.
# 전제: EC2 역할에 CloudWatchAgentServerPolicy 부착됨(0단계).
set -Eeuo pipefail

echo "== 1) 설치 =="
cd /tmp
wget -q https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/arm64/latest/amazon-cloudwatch-agent.deb
sudo dpkg -i amazon-cloudwatch-agent.deb
sudo mkdir -p /opt/aws/amazon-cloudwatch-agent/var

echo "== 2) 설정 배치 =="
sudo tee /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json > /dev/null <<'JSON'
{
  "agent": { "run_as_user": "root" },
  "metrics": {
    "namespace": "BidMate/Host",
    "append_dimensions": { "InstanceId": "${aws:InstanceId}" },
    "metrics_collected": {
      "cpu": { "measurement": ["usage_active"], "totalcpu": true },
      "mem": { "measurement": ["used_percent"] },
      "disk": { "measurement": ["used_percent"], "resources": ["/"] }
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/lib/docker/containers/*/*-json.log",
            "log_group_name": "/bidmate/api",
            "log_stream_name": "{instance_id}",
            "retention_in_days": 30
          }
        ]
      }
    },
    "metrics_collected": {
      "prometheus": {
        "log_group_name": "/bidmate/prometheus",
        "prometheus_config_path": "/opt/aws/amazon-cloudwatch-agent/var/prometheus.yaml",
        "emf_processor": {
          "metric_namespace": "BidMate/Backend",
          "metric_declaration": [
            {
              "source_labels": ["job"],
              "label_matcher": "^bidmate-backend$",
              "dimensions": [["slot"]],
              "metric_selectors": [
                "^bidmate_up$",
                "^bidmate_db_pool_.*$",
                "^bidmate_bid_stats_age_seconds$",
                "^bidmate_match_results_age_seconds$"
              ]
            }
          ]
        }
      }
    }
  }
}
JSON

sudo tee /opt/aws/amazon-cloudwatch-agent/var/prometheus.yaml > /dev/null <<'YAML'
global:
  scrape_interval: 30s
scrape_configs:
  - job_name: bidmate-backend
    metrics_path: /metrics
    static_configs:
      - targets: ["127.0.0.1:8000"]
        labels:
          slot: "green"
YAML

echo "== 3) 적용 + 시작 =="
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

echo "== 4) 상태 =="
sudo systemctl status amazon-cloudwatch-agent --no-pager | head -5
echo ">> 수 분 후 확인: aws cloudwatch list-metrics --region ap-northeast-2 --namespace BidMate/Backend"
