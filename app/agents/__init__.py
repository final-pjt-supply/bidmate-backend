# -*- coding: utf-8 -*-
# 에이전트 연결 코드 — HTTP 클라이언트 · 세션 왕복 서비스 · 챗 레이트리밋.
# 에이전트 로직 자체는 별도 서비스(bidmate-ai-agent, 루프백 8010의 POST /turn)에
# 있고, 여기는 백엔드 쪽 접점만 둔다. 백엔드가 bidmate-agents 패키지에서 쓰는 것은
# 계약 모듈(agents.schemas)뿐이다 — agents.run/agents.llm은 임포트하지 않는다.
# 대화·세션 컨텍스트 영속화는 infra의 ChatRepository 소관(ADR-22).
