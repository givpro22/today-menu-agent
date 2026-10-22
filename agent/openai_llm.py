"""LLM_BACKEND=openai — 실제 OpenAI API 백엔드 (fake_llm/ollama_llm과 동일 인터페이스).

fast(기본 gpt-4o-mini)는 라우팅·조건 추출·이유 검증·대화, quality(기본 gpt-4o-mini — 노트북
Before 와 같은 모델로 맞춤, 계약 §5-2)는 메뉴 추천 전담. 실모델이라 결정적이지 않다 —
verify(코드 판정)와 review(이유 검증)가 연출이 아니라 실제 방어선으로 동작한다.
실제 과금이 발생하는 원격 API이므로, 이 백엔드로 실행하면 OpenAI 계정에 비용이 청구된다.

구조화 출력은 OpenAI Chat Completions의 `response_format={"type": "json_object"}`
(JSON 모드)로 강제한다 — 스키마 자체를 강제하는 기능은 아니라서 시스템 프롬프트에 JSON
Schema 를 붙이고, 파싱 실패·필드 누락은 코드가 막는다: 1회 재시도 후 규칙 기반 폴백
(fake_llm.rule_*), 폴백 사실은 usage["fallback"] 으로 State·응답 metadata 에 남긴다.

비용(cost_usd)은 OpenAI 응답의 `usage.total_tokens`(실측) × 가상 단가다 — 세 백엔드를
같은 잣대로 비교하기 위해 단일 블렌디드 단가로 단순화한다.

사전 조건: 유효한 `OPENAI_API_KEY` 환경변수(.env).
"""
import logging
import os

import httpx
from langsmith import traceable
from pydantic import BaseModel

from . import prompts
from .fake_llm import rule_extract, rule_recommend, rule_route
from .schemas import Recommendation, ReviewResult, RouteResult, UserConstraints, parse_as

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
PARSE_ATTEMPTS = 2   # 최초 1회 + 재시도 1회
log = logging.getLogger("agent.openai_llm")


class OpenAILLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def _chat(self, system: str, user: str, json_mode: bool = False) -> tuple[str, dict]:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요."
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        resp = httpx.post(OPENAI_URL, json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage

    def _structured(self, op: str, system: str, user: str,
                    model_cls: type[BaseModel]) -> tuple[dict | None, dict]:
        """JSON 모드 호출 + 스키마 검증. 실패하면 1회 재시도, 그래도 실패면 (None, usage)."""
        system = f"{system}\n{prompts.json_instructions(model_cls)}"
        total = {"model": self.name, "tokens": 0, "usd": 0.0, "calls": 0,
                 "prompt": f"[SYSTEM]\n{system}\n\n[USER]\n{user}"}
        for _ in range(PARSE_ATTEMPTS):
            content, usage = self._chat(system, user, json_mode=True)
            total["tokens"] += usage["tokens"]
            total["usd"] = round(total["usd"] + usage["usd"], 6)
            total["calls"] += 1
            parsed = parse_as(content, model_cls)
            if parsed is not None:
                return parsed, total
            log.warning("%s: JSON 파싱 실패, 재시도", op)
        return None, total


class OpenAIFastLLM(OpenAILLM):
    """라우팅·조건 추출·이유 검증·단순 대화 전담 (저비용 모델, 기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_fast.route")
    def route(self, query: str) -> tuple[dict, dict]:
        result, usage = self._structured("route", prompts.ROUTE_SYSTEM, query, RouteResult)
        if result is None:
            # JSON 모드는 "유효한 JSON"만 보장할 뿐 값까지 강제하진 않는다 — 키워드 폴백
            usage["fallback"] = "route: JSON 파싱 2회 실패 → 키워드 라우팅"
            result = rule_route(query)
        return result, usage

    @traceable(run_type="llm", name="openai_fast.extract")
    def extract(self, query: str) -> tuple[dict, dict]:
        result, usage = self._structured("extract", prompts.EXTRACT_SYSTEM, query, UserConstraints)
        if result is None:
            usage["fallback"] = "extract: JSON 파싱 2회 실패 → 규칙 기반 추출"
            result = rule_extract(query)
        return result, usage

    @traceable(run_type="llm", name="openai_fast.review")
    def review(self, items: list[dict]) -> tuple[dict, dict]:
        result, usage = self._structured("review", prompts.REVIEW_SYSTEM,
                                         prompts.review_user(items), ReviewResult)
        if result is None:
            usage["fallback"] = "review: JSON 파싱 2회 실패 → LLM 판정 생략(규칙 검사만)"
            result = {"verdicts": [{"name": it["name"], "contradiction": False, "evidence": ""}
                                   for it in items]}
        return result, usage

    @traceable(run_type="llm", name="openai_fast.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """ollama_llm.py와 인터페이스(on_token 콜백)만 맞춘다 — stream=True 로 호출하지 않아
        토큰 단위 중계는 하지 않고 완성된 응답을 한 번에 전달한다."""
        user = prompts.chat_user(query, history)
        content, usage = self._chat(prompts.CHAT_SYSTEM, user)
        usage["prompt"] = f"[SYSTEM]\n{prompts.CHAT_SYSTEM}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage


class OpenAIQualityLLM(OpenAILLM):
    """메뉴 추천 전담 (기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_quality.recommend")
    def recommend(self, query: str, constraints: dict, relaxed: list[dict],
                  candidates: list[dict]) -> tuple[dict, dict]:
        user = prompts.recommend_user(query, constraints, relaxed, candidates)
        result, usage = self._structured("recommend", prompts.RECOMMEND_SYSTEM, user,
                                         Recommendation)
        if result is None:
            usage["fallback"] = "recommend: JSON 파싱 2회 실패 → 후보 앞 3개 템플릿 추천"
            result = rule_recommend(candidates)
        return result, usage
