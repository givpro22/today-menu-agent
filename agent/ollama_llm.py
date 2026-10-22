"""LLM_BACKEND=ollama — 로컬 Ollama 실모델 백엔드 (fake_llm과 동일 인터페이스).

fast(기본 qwen3:0.6b)는 라우팅·조건 추출·이유 검증·대화, quality(기본 qwen3:8b)는 메뉴 추천.
Ollama의 구조화 출력(format=<JSON Schema>)과 temperature=0으로 변동을 줄이지만 실모델이라
결정적이지 않다. 특히 0.6b 같은 작은 모델은 스키마를 자주 깨뜨린다 — 파싱 실패는 1회
재시도 후 규칙 기반 폴백(fake_llm.rule_*)으로 넘기고, 로그와 usage["fallback"] 에 남긴다.

비용(cost_usd)은 '가상 단가 × 실측 토큰 수(prompt_eval_count+eval_count)'다 — 로컬
추론이라 실제 과금은 0이지만, 세 백엔드를 같은 잣대로 비교하기 위함이다.

사전 조건: `ollama serve` 실행 중 + `ollama pull qwen3:0.6b` 및 `ollama pull qwen3:8b`.
"""
import json
import logging

import httpx
from langsmith import traceable
from pydantic import BaseModel

from . import prompts
from .fake_llm import rule_extract, rule_recommend, rule_route
from .schemas import Recommendation, ReviewResult, RouteResult, UserConstraints, parse_as

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
PARSE_ATTEMPTS = 2   # 최초 1회 + 재시도 1회
log = logging.getLogger("agent.ollama_llm")


class OllamaLLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"ollama:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens

    def _chat(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        if schema is not None:
            payload["format"] = schema
        resp = httpx.post(OLLAMA_URL, json=payload, timeout=300.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["message"]["content"]
        tokens = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage

    def _structured(self, op: str, system: str, user: str,
                    model_cls: type[BaseModel]) -> tuple[dict | None, dict]:
        """스키마 강제 호출 + 검증. 실패하면 1회 재시도, 그래도 실패면 (None, usage)."""
        schema = model_cls.model_json_schema()
        total = {"model": self.name, "tokens": 0, "usd": 0.0, "calls": 0,
                 "prompt": f"[SYSTEM]\n{system}\n\n[USER]\n{user}"}
        for _ in range(PARSE_ATTEMPTS):
            content, usage = self._chat(system, user, schema=schema)
            total["tokens"] += usage["tokens"]
            total["usd"] = round(total["usd"] + usage["usd"], 6)
            total["calls"] += 1
            parsed = parse_as(content, model_cls)
            if parsed is not None:
                return parsed, total
            log.warning("%s: 구조화 출력 파싱 실패, 재시도", op)
        return None, total

    async def _chat_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """사용자 대면 텍스트(chat) 전용 — Ollama NDJSON 스트림을 토큰 단위로 받아
        on_token(조각)을 즉시 호출해 SSE로 실시간 중계할 수 있게 한다. JSON 스키마 출력은
        부분 출력이 의미가 없어 _chat 을 쓴다."""
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": True,
            "options": {"temperature": 0.0},
        }
        chunks: list[str] = []
        usage = {"model": self.name, "tokens": 0, "usd": 0.0}
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                        if on_token is not None:
                            on_token(piece)
                    if data.get("done"):
                        tokens = ((data.get("prompt_eval_count") or 0)
                                  + (data.get("eval_count") or 0))
                        usage = {"model": self.name, "tokens": tokens,
                                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        return "".join(chunks), usage


class OllamaFastLLM(OllamaLLM):
    """라우팅·조건 추출·이유 검증·단순 대화 전담 (저사양 소형 모델)."""

    @traceable(run_type="llm", name="ollama_fast.route")
    def route(self, query: str) -> tuple[dict, dict]:
        result, usage = self._structured("route", prompts.ROUTE_SYSTEM, query, RouteResult)
        if result is None:
            # 소형 모델의 형식 붕괴 대비 — 코드 레벨 fallback
            usage["fallback"] = "route: 구조화 출력 2회 실패 → 키워드 라우팅"
            result = rule_route(query)
        return result, usage

    @traceable(run_type="llm", name="ollama_fast.extract")
    def extract(self, query: str) -> tuple[dict, dict]:
        result, usage = self._structured("extract", prompts.EXTRACT_SYSTEM, query, UserConstraints)
        if result is None:
            usage["fallback"] = "extract: 구조화 출력 2회 실패 → 규칙 기반 추출"
            result = rule_extract(query)
        return result, usage

    @traceable(run_type="llm", name="ollama_fast.review")
    def review(self, items: list[dict]) -> tuple[dict, dict]:
        result, usage = self._structured("review", prompts.REVIEW_SYSTEM,
                                         prompts.review_user(items), ReviewResult)
        if result is None:
            usage["fallback"] = "review: 구조화 출력 2회 실패 → LLM 판정 생략(규칙 검사만)"
            result = {"verdicts": [{"name": it["name"], "contradiction": False, "evidence": ""}
                                   for it in items]}
        return result, usage

    @traceable(run_type="llm", name="ollama_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(prompts.CHAT_SYSTEM, prompts.chat_user(query, history),
                                       on_token=on_token)


class OllamaQualityLLM(OllamaLLM):
    """메뉴 추천 전담 (상대적으로 큰 모델)."""

    @traceable(run_type="llm", name="ollama_quality.recommend")
    def recommend(self, query: str, constraints: dict, relaxed: list[dict],
                  candidates: list[dict]) -> tuple[dict, dict]:
        user = prompts.recommend_user(query, constraints, relaxed, candidates)
        result, usage = self._structured("recommend", prompts.RECOMMEND_SYSTEM, user,
                                         Recommendation)
        if result is None:
            usage["fallback"] = "recommend: 구조화 출력 2회 실패 → 후보 앞 3개 템플릿 추천"
            result = rule_recommend(candidates)
        return result, usage
