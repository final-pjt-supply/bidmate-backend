# -*- coding: utf-8 -*-
"""Cloudflare 임베딩 + OpenSearch 제목 벡터 검색 어댑터."""
import json
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

import httpx

# 텍스트→벡터 캐시 상한. bge-m3는 1024차원이고 파이썬 list[float] 하나가 약 32KB라
# 512개면 대략 16MB다. 회사당 관심 쿼리가 최대 10개(_MAX_INTEREST_QUERIES)이므로
# 활성 회사 수십 곳을 담기에 충분하다.
_EMBEDDING_CACHE_SIZE = 512

# 추천은 동기 엔드포인트라 외부 호출(임베딩·kNN) 내내 DB 커넥션을 쥔다. 죽은/느린
# 외부가 오래 매달리면 커넥션 풀이 소진돼 전체 API로 번진다. connect는 짧게(3s —
# 정상 연결은 <1s, 넘으면 사실상 미도달) 조여 캐스케이드를 끊고, read는 완만히(30s,
# 정상 응답엔 충분한 여유)로 둔다. 더 공격적인 read/재시도는 p95 측정 후.
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=3.0)


class RecommendationSearchError(RuntimeError):
    """외부 임베딩/검색 시스템을 사용할 수 없을 때."""


@dataclass(frozen=True)
class TitleSearchHit:
    bid_id: str
    score: float


class _EmbeddingCache:
    """텍스트→벡터 프로세스 내 LRU 캐시.

    TTL이 없다. 같은 모델에서 같은 텍스트의 임베딩은 항상 같은 벡터라 낡을 일이
    없고, 스크랩·실적이 바뀌면 텍스트 자체가 달라져 키가 바뀌므로 무효화도 저절로
    된다. 즉 만료 대신 LRU 축출만 있으면 된다.

    엔드포인트가 async가 아닌 def라 FastAPI가 스레드풀에서 돌린다. 요청이 겹치면
    같은 캐시를 여러 스레드가 만지므로 락이 필요하다.
    """

    def __init__(self, maxsize: int = _EMBEDDING_CACHE_SIZE):
        self._maxsize = maxsize
        self._store: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = Lock()

    def get_many(self, texts: list[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        with self._lock:
            for text in texts:
                vector = self._store.get(text)
                if vector is not None:
                    self._store.move_to_end(text)
                    found[text] = vector
        return found

    def put_many(self, pairs) -> None:
        with self._lock:
            for text, vector in pairs:
                self._store[text] = vector
                self._store.move_to_end(text)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)


class RecommendationSearch:
    def __init__(
        self,
        *,
        opensearch_url: str,
        opensearch_user: str,
        opensearch_password: str,
        index_name: str,
        verify_certs: bool,
        cf_account_id: str,
        cf_api_token: str,
        cf_model: str,
    ):
        self._opensearch_url = opensearch_url.rstrip("/")
        self._auth = (
            (opensearch_user, opensearch_password)
            if opensearch_user or opensearch_password
            else None
        )
        self._index_name = index_name
        self._verify_certs = verify_certs
        self._cf_account_id = cf_account_id
        self._cf_api_token = cf_api_token
        self._cf_model = cf_model
        self._cache = _EmbeddingCache()
        # 커넥션을 유지한다. 모듈 함수(httpx.post)는 호출마다 TLS 핸드셰이크를 다시
        # 해서, 임베딩 한 번이 0.65s → 0.16s로 줄어드는 차이가 난다. 어댑터는
        # deps에서 프로세스당 하나만 만들어야 이 재사용이 실제로 일어난다.
        self._cf_client = httpx.Client(timeout=_HTTP_TIMEOUT)
        self._os_client = httpx.Client(
            timeout=_HTTP_TIMEOUT, auth=self._auth, verify=verify_certs
        )

    def close(self) -> None:
        self._cf_client.close()
        self._os_client.close()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """캐시에 없는 텍스트만 원격 임베딩한다. 입력 순서를 그대로 돌려준다."""
        if not texts:
            return []
        cached = self._cache.get_many(texts)
        # 같은 텍스트가 두 번 들어와도 한 번만 보낸다. dict.fromkeys로 순서를 지킨다.
        missing = [text for text in dict.fromkeys(texts) if text not in cached]
        if missing:
            vectors = self._embed_remote(missing)
            if len(vectors) != len(missing):
                raise RecommendationSearchError("임베딩 API 응답 형식이 올바르지 않습니다.")
            self._cache.put_many(zip(missing, vectors))
            cached.update(zip(missing, vectors))
        return [cached[text] for text in texts]

    def _embed_remote(self, texts: list[str]) -> list[list[float]]:
        if not self._cf_account_id or not self._cf_api_token:
            raise RecommendationSearchError("추천 임베딩 설정이 없습니다.")
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._cf_account_id}/ai/run/{self._cf_model}"
        )
        try:
            response = self._cf_client.post(
                url,
                headers={"Authorization": f"Bearer {self._cf_api_token}"},
                json={"text": texts},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RecommendationSearchError("관심 텍스트 임베딩에 실패했습니다.") from exc
        if not payload.get("success") or not payload.get("result", {}).get("data"):
            raise RecommendationSearchError("임베딩 API 응답 형식이 올바르지 않습니다.")
        return payload["result"]["data"]

    def search_titles_many(
        self,
        vectors: list[list[float]],
        *,
        candidate_ids: list[str],
        limit: int,
    ) -> list[list[TitleSearchHit]]:
        """벡터별 제목 kNN 결과를 입력 순서대로 반환한다.

        관심 쿼리마다 _search를 따로 던지면 왕복이 쿼리 수만큼 직렬로 쌓인다
        (상한 10개). _msearch로 묶으면 왕복이 한 번이다. 전송 바이트는 줄지 않는다 —
        서브쿼리마다 후보 필터를 각자 실어야 하기 때문이다.
        """
        if not vectors:
            return []
        if not candidate_ids:
            return [[] for _ in vectors]

        search_size = min(len(candidate_ids), max(limit * 5, 50))
        # 후보 필터는 모든 서브쿼리가 동일하다. 한 번만 만들어 재사용한다.
        query_filter = {
            "bool": {
                "filter": [
                    {"terms": {"bid_id": candidate_ids}},
                    {"term": {"type": "title"}},
                ]
            }
        }
        lines: list[str] = []
        for vector in vectors:
            # 헤더 줄 — 인덱스를 URL에 적었으므로 비워 둔다.
            lines.append("{}")
            lines.append(
                json.dumps(
                    {
                        "size": search_size,
                        "query": {
                            "knn": {
                                "vector": {
                                    "vector": vector,
                                    "k": search_size,
                                    "filter": query_filter,
                                }
                            }
                        },
                        "_source": ["bid_id"],
                    },
                    ensure_ascii=False,
                )
            )
        # NDJSON은 마지막 줄바꿈까지 있어야 한다. 빠지면 OpenSearch가 요청을 거부한다.
        body = "\n".join(lines) + "\n"

        try:
            response = self._os_client.post(
                f"{self._opensearch_url}/{self._index_name}/_msearch",
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RecommendationSearchError("추천 제목 검색에 실패했습니다.") from exc

        responses = payload.get("responses")
        # 개수가 어긋나면 결과를 엉뚱한 관심 쿼리에 붙이게 된다. 짝이 안 맞으면 멈춘다.
        if not isinstance(responses, list) or len(responses) != len(vectors):
            raise RecommendationSearchError("추천 제목 검색 응답이 요청과 맞지 않습니다.")
        return [self._parse_hits(item) for item in responses]

    @staticmethod
    def _parse_hits(item: object) -> list[TitleSearchHit]:
        # _msearch는 서브검색 하나가 실패해도 전체 HTTP는 200이고 그 항목에만 error가
        # 들어온다. 여기서 걸러내지 않으면 실패를 '결과 0건'으로 착각해, 추천이 조용히
        # 비거나 순위가 뒤틀린 채로 나간다. 기존 _search와 같이 실패는 실패로 올린다.
        if not isinstance(item, dict) or "error" in item:
            raise RecommendationSearchError("추천 제목 검색에 실패했습니다.")
        return [
            TitleSearchHit(
                bid_id=hit["_source"]["bid_id"],
                score=float(hit["_score"]),
            )
            for hit in item.get("hits", {}).get("hits", [])
            if hit.get("_source", {}).get("bid_id")
        ]
