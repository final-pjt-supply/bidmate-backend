# -*- coding: utf-8 -*-
"""S3EventSink 단위 테스트 — 배칭·NDJSON·날짜 파티션 키·실패 재큐 (fake boto3 클라)."""
import json

from app.infra.s3.event_sink import S3EventSink


class FakeS3:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def put_object(self, Bucket, Key, Body):
        if self.fail:
            raise RuntimeError("AccessDenied")
        self.calls.append({"Bucket": Bucket, "Key": Key, "Body": Body})


def test_flush_writes_ndjson_with_date_partition():
    s3 = FakeS3()
    sink = S3EventSink(s3, bucket="bidmate", prefix="user_events", flush_max=100)
    sink.add({"event_name": "home_viewed", "anonymous_id": "a"})
    sink.add({"event_name": "bid_card_clicked", "anonymous_id": "b"})
    assert sink.flush() == 2
    assert len(s3.calls) == 1
    call = s3.calls[0]
    assert call["Bucket"] == "bidmate"
    assert call["Key"].startswith("user_events/dt=")   # 날짜 파티션
    assert call["Key"].endswith(".json")
    lines = call["Body"].decode("utf-8").strip().split("\n")   # NDJSON
    assert len(lines) == 2
    assert json.loads(lines[0])["event_name"] == "home_viewed"


def test_threshold_flush_auto():
    s3 = FakeS3()
    sink = S3EventSink(s3, bucket="b", flush_max=2)
    sink.add({"x": 1})
    assert len(s3.calls) == 0     # 임계 미도달
    sink.add({"x": 2})            # 임계 도달 → 자동 flush
    assert len(s3.calls) == 1


def test_empty_flush_is_noop():
    s3 = FakeS3()
    sink = S3EventSink(s3, bucket="b")
    assert sink.flush() == 0
    assert s3.calls == []


def test_failed_put_requeues_for_retry():
    # PutObject 실패(예: IAM 미부여) 시 배치를 되돌려 다음 flush에서 재시도(유실 방지).
    sink = S3EventSink(FakeS3(fail=True), bucket="b", flush_max=100)
    sink.add({"x": 1})
    try:
        sink.flush()
    except RuntimeError:
        pass
    ok = FakeS3()
    sink._client = ok            # 권한 붙었다고 가정하고 정상 클라로 교체
    assert sink.flush() == 1     # 재큐된 이벤트가 유실 없이 올라감
    assert len(ok.calls) == 1
