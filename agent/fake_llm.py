"""결정적 Fake LLM 2종 (fast/quality) + 실측 비용 계산 — sample 구조 유지, 메뉴 도메인으로 교체.

실제 LLM 호출 없이 그래프 전체가 결정적으로 재현되도록 문장 키워드로 조건을 만들고,
후보 앞에서부터 3개를 고른다. 각 호출은 LangSmith 에 run_type="llm" 으로 기록되고,
문자열 길이 기반 토큰 추정치로 비용을 계산한다.

테스트 모드(`mode`, server 가 env FAKE_LLM_MODE 로 넘김) — 노트북 6-5 를 테스트로 승격:
- "off_menu": 첫 recommend 에서 1개를 후보 밖 이름("없는메뉴")으로 → verify 가 빼고 재추천
- "bad_reason": 맵기 2 미만 메뉴 하나의 이유를 "매운맛이 강해..." 로 → review rules 가 잡음

`rule_route()` / `rule_extract()` / `rule_recommend()` 는 ollama/openai 파싱 폴백에서도 쓰는 공개 함수다.
"""
import json
import re

from langsmith import traceable

OFF_MENU_NAME = "없는메뉴"
BAD_REASON = "매운맛이 강해 스트레스가 풀려요."

_NUM_WORDS = {"한": 1, "혼자": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4,
              "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
# 어제 먹은 음식 → 같은 재료(노트북 추출 규칙 2)
_YESTERDAY_FOOD = {"치킨": "닭고기", "삼겹살": "돼지고기", "족발": "돼지고기", "보쌈": "돼지고기",
                   "피자": "밀가루", "회": "생선", "짜장면": "밀가루", "라면": "밀가루"}
_PREFER_RULES = [
    (r"속이 (안 좋|별로|불편|쓰려)|숙취|술 (먹|마시)", "속편한"),   # 노트북 추출 규칙 3
    (r"국물", "국물"),
    (r"해장", "해장"),
    (r"든든", "든든한"),
    (r"담백", "담백한"),
    (r"가볍게|가벼운", "가벼운"),
    (r"빨리|빠르게|급해", "빠른"),
    (r"매운 ?(게|거|걸|것) ?(당|먹고|좋)|맵게|매콤", "매운"),
]
_AVOID_RULES = [
    (r"(기름진|느끼한|기름기 많은) ?(건|거|것|음식은?)? ?(못|싫|빼|말고|별로|피)", "기름진"),
]


def _to_int_people(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    return _NUM_WORDS.get(word)


def _parse_people(q: str) -> int:
    if re.search(r"혼자|혼밥", q):
        return 1
    m = re.search(r"(\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*명", q)
    if not m:
        return 1
    n = _to_int_people(m.group(1)) or 1
    # "친구 세 명이랑" 은 나를 빼고 센 인원 → +1. "네 명이서" 는 전체 인원.
    if re.match(r"(이랑|랑|하고|과|와)", q[m.end():]):
        n += 1
    return n


def _parse_won(q: str) -> int | None:
    m = re.search(r"(\d+)\s*만\s*(\d+)\s*천\s*원", q)
    if m:
        return int(m.group(1)) * 10000 + int(m.group(2)) * 1000
    m = re.search(r"(\d[\d,]*)\s*원", q)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"(\d+)\s*만\s*원", q)
    if m:
        return int(m.group(1)) * 10000
    m = re.search(r"(\d+)\s*천\s*원", q)
    if m:
        return int(m.group(1)) * 1000
    if re.search(r"(?<![\d가-힣])만\s*원", q) or re.search(r"(^|\s)만원", q):
        return 10000
    return None


def _parse_minutes(q: str) -> int | None:
    if re.search(r"시간(은|이)? ?(넉넉|상관없|많)", q):
        return None
    m = re.search(r"(\d+)\s*분", q)
    if m:
        return int(m.group(1))
    if re.search(r"한 ?시간", q):
        return 60
    if re.search(r"반 ?시간", q):
        return 30
    return None


def _parse_spicy(q: str) -> bool | None:
    if re.search(r"안 ?매운|맵지 않|매운 ?(건|거|걸|것|음식은?) ?(못|싫|별로|피|빼)|맵찔이", q):
        return False
    if re.search(r"매운 ?(게|거|걸|것) ?(당|먹고|좋)|맵게|매콤|얼큰", q):
        return True
    return None


def _parse_allergies(q: str) -> list[str]:
    out = []
    for m in re.finditer(r"((?:[가-힣]+(?:이랑|랑|하고|,)\s*)*[가-힣]+)\s*알레르기", q):
        for word in re.split(r"이랑|랑|하고|,", m.group(1)):
            word = word.strip()
            if word and word not in out:
                out.append(word)
    return out


def _parse_meal(q: str) -> str | None:
    m = re.search(r"아침|점심|저녁|간식", q)
    return m.group(0) if m else None


def rule_route(query: str) -> dict:
    """키워드 라우팅 — fake 의 route 이자 실모델 파싱 실패 시 폴백."""
    menu = bool(re.search(
        r"먹|메뉴|점심|저녁|아침|야식|간식|배고|식사|추천|예산|알레르기|해장|매운|맵", query))
    return {"route": "menu" if menu else "chat"}


def rule_extract(query: str) -> dict:
    """규칙 기반 조건 추출 — fake 의 extract 이자 실모델 파싱 실패 시 폴백.
    반환은 UserConstraints dict (normalize_allergens 전 원값)."""
    people = _parse_people(query)
    budget = _parse_won(query)
    if budget is not None and people > 1 and not re.search(r"1인|인당|한 ?명당|1명당", query):
        budget = budget // people   # 노트북 추출 규칙 5: 총액은 인원수로 나눈다
    avoid = [kw for pat, kw in _AVOID_RULES if re.search(pat, query)]
    if re.search(r"어제|전날|지난번", query):
        for food, ingredient in _YESTERDAY_FOOD.items():
            if food in query and ingredient not in avoid:
                avoid.append(ingredient)
    prefer = [kw for pat, kw in _PREFER_RULES if re.search(pat, query)]
    return {"budget": budget, "max_minutes": _parse_minutes(query),
            "spicy_ok": _parse_spicy(query), "avoid_keywords": avoid,
            "prefer_keywords": prefer, "allergies": _parse_allergies(query),
            "people": people, "meal": _parse_meal(query)}


def template_reason(menu: dict) -> str:
    """메뉴 데이터만으로 만든 이유 문장 — 숫자·맵기 표현 없이 실제 tags/main 만 쓴다."""
    tags = ", ".join(menu["tags"][:2])
    return f"{tags} 특징에 {menu['main'][0]} 재료가 들어가 지금 상황에 잘 맞아요."


def rule_recommend(candidates: list[dict]) -> dict:
    """후보 앞에서부터 최대 3개 — fake 의 recommend 이자 실모델 파싱 실패 시 폴백."""
    picks = [{"name": m["name"], "reason": template_reason(m)} for m in candidates[:3]]
    return {"picks": picks,
            "summary": "조건을 지키는 후보 중 상황에 가장 잘 맞는 메뉴로 골랐어요." if picks else ""}


class FakeLLM:
    def __init__(self, name: str, usd_per_1k_tokens: float, mode: str = "normal"):
        self.name = name
        self.usd_per_1k = usd_per_1k_tokens
        self.mode = mode

    def _usage(self, prompt: str, completion: str) -> dict:
        tokens = (len(prompt) + len(completion)) // 4 + 1
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6)}


class FastLLM(FakeLLM):
    """분류·추출·리뷰·대화 등 짧고 정형적인 단계 전담 (저비용)."""

    @traceable(run_type="llm", name="fast_llm.route")
    def route(self, query: str) -> tuple[dict, dict]:
        result = rule_route(query)
        return result, self._usage(query, str(result))

    @traceable(run_type="llm", name="fast_llm.extract")
    def extract(self, query: str) -> tuple[dict, dict]:
        result = rule_extract(query)
        return result, self._usage(query, json.dumps(result, ensure_ascii=False))

    @traceable(run_type="llm", name="fast_llm.review")
    def review(self, items: list[dict]) -> tuple[dict, dict]:
        """rules 단계가 잡은 것 외에는 모두 통과(결정성)."""
        result = {"verdicts": [{"name": it["name"], "contradiction": False, "evidence": ""}
                               for it in items]}
        return result, self._usage(str(items), str(result))

    @traceable(run_type="llm", name="fast_llm.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """sample 그대로 — 히스토리에서 이름을 기억해낸다. fake 는 토큰 단위로 쪼갤 대상이
        없어 on_token 인터페이스만 맞추고 결과를 한 번에 보낸다."""
        for msg in reversed(history):
            m = re.search(r"제 이름은 (\S+?)입니다", msg.get("content", ""))
            if m and re.search(r"이름이 뭐", query):
                reply = f"{m.group(1)}님이라고 말씀하셨어요. 무엇을 도와드릴까요?"
                if on_token is not None:
                    on_token(reply)
                return reply, self._usage(query, reply)
        m = re.search(r"제 이름은 (\S+?)입니다", query)
        if m:
            reply = f"안녕하세요, {m.group(1)}님! 오늘 어떤 메뉴를 찾아 드릴까요?"
        else:
            reply = ("안녕하세요! 오늘뭐먹지 도우미예요. 식사 상황을 한 문장으로 적어 주시면 "
                     "조건에 맞는 메뉴를 3개까지 추천해 드려요.")
        if on_token is not None:
            on_token(reply)
        return reply, self._usage(query, reply)


class QualityLLM(FakeLLM):
    """최종 추천 전담 (고품질·고비용)."""

    def __init__(self, name: str, usd_per_1k_tokens: float, mode: str = "normal"):
        super().__init__(name, usd_per_1k_tokens, mode)
        # off_menu: 같은 요청의 첫 호출에만 후보 밖 메뉴를 섞는다. 요청 키별로 토글하므로
        # 다른 요청이 사이에 끼어도 결정적이다.
        self._off_menu_pending: set[str] = set()

    @traceable(run_type="llm", name="quality_llm.recommend")
    def recommend(self, query: str, constraints: dict, relaxed: list[dict],
                  candidates: list[dict]) -> tuple[dict, dict]:
        result = rule_recommend(candidates)
        picks = result["picks"]
        if self.mode == "off_menu" and picks:
            key = query + json.dumps(constraints, ensure_ascii=False, sort_keys=True)
            if key in self._off_menu_pending:
                self._off_menu_pending.discard(key)       # 재추천 → 정상
            else:
                self._off_menu_pending.add(key)           # 첫 추천 → 마지막 1개를 후보 밖으로
                picks[-1] = {"name": OFF_MENU_NAME, "reason": "후보에 없는 메뉴예요."}
        elif self.mode == "bad_reason":
            # 맵기 2 미만인 추천 하나(두 번째 우선, 없으면 첫 번째)의 이유를 틀리게 쓴다
            by_name = {m["name"]: m for m in candidates}
            order = picks[1:] + picks[:1]
            target = next((p for p in order if by_name[p["name"]]["spicy"] < 2), None)
            if target is not None:
                target["reason"] = BAD_REASON
        return result, self._usage(query + str(candidates), str(result))
