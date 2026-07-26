# -*- coding: utf-8 -*-
"""Cloudflare 임베딩 + OpenSearch 제목 벡터 검색 어댑터."""
from dataclasses import dataclass

import httpx


class RecommendationSearchError(RuntimeError):
    """외부 임베딩/검색 시스템을 사용할 수 없을 때."""


@dataclass(frozen=True)
class TitleSearchHit:
    bid_id: str
    score: float


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

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._cf_account_id or not self._cf_api_token:
            raise RecommendationSearchError("추천 임베딩 설정이 없습니다.")
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._cf_account_id}/ai/run/{self._cf_model}"
        )
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self._cf_api_token}"},
                json={"text": texts},
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RecommendationSearchError("관심 텍스트 임베딩에 실패했습니다.") from exc
        if not payload.get("success") or not payload.get("result", {}).get("data"):
            raise RecommendationSearchError("임베딩 API 응답 형식이 올바르지 않습니다.")
        return payload["result"]["data"]

    def search_titles(
        self,
        vector: list[float],
        *,
        candidate_ids: list[str],
        limit: int,
    ) -> list[TitleSearchHit]:
        if not candidate_ids:
            return []
        search_size = min(len(candidate_ids), max(limit * 5, 50))
        body = {
            "size": search_size,
            "query": {
                "knn": {
                    "vector": {
                        "vector": vector,
                        "k": search_size,
                        "filter": {
                            "bool": {
                                "filter": [
                                    {"terms": {"bid_id": candidate_ids}},
                                    {"term": {"type": "title"}},
                                ]
                            }
                        },
                    }
                }
            },
            "_source": ["bid_id"],
        }
        try:
            response = httpx.post(
                f"{self._opensearch_url}/{self._index_name}/_search",
                auth=self._auth,
                verify=self._verify_certs,
                json=body,
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RecommendationSearchError("추천 제목 검색에 실패했습니다.") from exc
        return [
            TitleSearchHit(
                bid_id=hit["_source"]["bid_id"],
                score=float(hit["_score"]),
            )
            for hit in payload.get("hits", {}).get("hits", [])
            if hit.get("_source", {}).get("bid_id")
        ]
