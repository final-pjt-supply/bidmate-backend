# -*- coding: utf-8 -*-
"""고객 여정 이벤트 → S3 배치 적재(NDJSON, 날짜 파티션).

왜 S3인가: 분석 부하를 운영 DB(공유 RDS)에서 분리한다. 단, 이벤트 1건당 객체 1개는
tiny-file 문제(Athena 느림 + PUT 비용)라 **메모리 버퍼에 모아 배치로** 올린다.

경로: s3://<bucket>/<prefix>/dt=YYYY-MM-DD/HHMMSS-<rand>.json (한 줄 = 한 이벤트)

한계(테스트/MVP): 인메모리 버퍼라 프로세스 재시작 시 미flush분은 유실(best-effort).
전송 실패한 배치는 다시 버퍼에 넣어 다음 주기에 재시도한다(IAM 붙기 전엔 쌓였다가
붙는 순간 올라감). 상한(_MAX_BUFFER) 초과 시 오래된 것부터 버린다(메모리 보호).
스케일에선 Kinesis Firehose로 대체(durable·자동 파티션). 단일 uvicorn 프로세스 전제.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

_KST = timezone(timedelta(hours=9))
_MAX_BUFFER = 50_000   # 재시도 누적 상한(메모리 보호)


class S3EventSink:
    def __init__(
        self,
        client: Any,
        bucket: str,
        prefix: str = "user_events",
        flush_max: int = 20,
        flush_interval: float = 15.0,
    ):
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._flush_max = flush_max
        self._flush_interval = flush_interval
        self._buf: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, event: dict) -> None:
        """이벤트 1건 버퍼에 추가. 임계 도달 시 즉시 flush. 절대 예외를 던지지
        않는다(수집 실패가 API 응답을 막으면 안 됨 — best-effort)."""
        with self._lock:
            self._buf.append(event)
            if len(self._buf) > _MAX_BUFFER:
                self._buf = self._buf[-_MAX_BUFFER:]   # 오래된 것부터 버림
            due = len(self._buf) >= self._flush_max
        if due:
            try:
                self.flush()
            except Exception:
                pass   # 실패분은 flush()가 재큐함 — 다음 주기 재시도

    def flush(self) -> int:
        """버퍼를 NDJSON 한 파일로 S3에 올린다. 실패 시 배치를 되돌려 재시도 대상으로."""
        with self._lock:
            if not self._buf:
                return 0
            batch, self._buf = self._buf, []
        body = ("\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in batch) + "\n").encode("utf-8")
        now = datetime.now(_KST)
        key = f"{self._prefix}/dt={now:%Y-%m-%d}/{now:%H%M%S}-{uuid.uuid4().hex[:8]}.json"
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=body)
        except Exception:
            with self._lock:                       # 재큐(앞쪽에 되돌림)
                self._buf = batch + self._buf
                if len(self._buf) > _MAX_BUFFER:
                    self._buf = self._buf[-_MAX_BUFFER:]
            raise
        return len(batch)

    # --- 백그라운드 주기 flush (저볼륨에서도 이벤트가 S3에 도달하도록) ---
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="event-flusher", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval):
            try:
                self.flush()
            except Exception:
                pass   # best-effort: 다음 주기 재시도

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        try:
            self.flush()   # 종료 시 남은 것 마저
        except Exception:
            pass


_sink: S3EventSink | None = None


def get_event_sink() -> S3EventSink:
    """프로세스 1개짜리 싱글턴 sink. boto3는 이 시점에 지연 import(로컬/테스트에서
    boto3 없이도 앱 임포트가 되게)."""
    global _sink
    if _sink is None:
        import boto3   # 지연 import

        from app.config import get_settings

        s = get_settings()
        client = boto3.client("s3", region_name=(s.aws_region or None))
        _sink = S3EventSink(
            client,
            bucket=s.s3_bucket,
            prefix=s.s3_events_prefix,
            flush_max=s.event_flush_max,
            flush_interval=s.event_flush_interval_sec,
        )
    return _sink
