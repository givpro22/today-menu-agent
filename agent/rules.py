"""판정 규칙 — 노트북 5-2(알레르기 표기), 5-6(판정 함수), 5-6 relax 계산 이식. 순수 함수.

filter 와 verify 가 이 파일의 같은 함수를 import 한다(사본 금지). 둘이 다르게 판정하면
알레르기 메뉴가 새어 나간다. 판정 기준의 원천은 menu-domain-spec 스킬이다.
LLM·IO 의존이 없어서 pytest 로 바로 검증된다.
"""
from .schemas import UserConstraints

RECENT_LIMIT = 6             # 최근 추천 기억 개수(두 번 전 요청까지)
MEAL_EXCLUDE_DAYS = 3        # 최근 며칠 먹은 메뉴를 뺄지
MAX_RELAX = 2                # 조건 완화 상한
MAX_RECOMMEND_ATTEMPTS = 2   # recommend 상한(재추천 1회)
CANDIDATE_LIMIT = 10         # LLM 에 넘길 후보 수
MAX_PICKS = 3                # 추천 개수

_RELAX_FIELDS = {
    "budget": {"label": "예산", "unit": "원"},
    "max_minutes": {"label": "시간", "unit": "분"},
}


def normalize_allergens(items: list[str], alias: dict[str, str | list[str]]) -> list[str]:
    """표기를 데이터 쪽으로 맞춘다(밀가루→밀). alias 값이 리스트면 펼친다 — 묶음 표기
    ("해산물" → 새우, 오징어, 조개)를 하나로만 바꾸면 나머지가 새어 나간다(QA F2-1).
    노트북과 달리 순서를 지키며 중복을 지운다 — "밀가루, 면, 빵" 이 ["밀", "밀", "밀"] 이 되지 않게."""
    out = []
    for a in items:
        if not a or not a.strip():
            continue
        mapped = alias.get(a.strip(), a.strip())
        out.extend([mapped] if isinstance(mapped, str) else mapped)
    return list(dict.fromkeys(out))


def block_reason(m: dict, c: UserConstraints, recent: list[str],
                 eaten: list[str] = ()) -> str | None:
    """풀지 않는 조건. 알레르기·맵기·끼니는 건강 문제이거나 사용자가 분명히 말한 조건이다."""
    if any(a in m["allergens"] for a in c.allergies):
        return "알레르기 재료 포함"
    if c.spicy_ok is False and m["spicy"] >= 2:
        return "매운 메뉴"
    if c.meal in ("점심", "저녁") and "간식" in m["tags"]:
        return "끼니가 아닌 간식"
    if m["name"] in recent:
        return "직전에 추천한 메뉴"
    if m["name"] in eaten:
        return "최근에 먹은 메뉴"
    return None


def over_limit(m: dict, c: UserConstraints) -> str | None:
    """relax 가 풀 수 있는 조건(예산, 시간)."""
    if c.budget is not None and m["price"] > c.budget:
        return f"예산 초과 {m['price']}원"
    if c.max_minutes is not None and m["minutes"] > c.max_minutes:
        return f"시간 초과 {m['minutes']}분"
    return None


def match_score(m: dict, c: UserConstraints) -> int:
    words = m["tags"] + m["main"] + [m["category"]]
    score = sum(k in words for k in c.prefer_keywords) - sum(k in words for k in c.avoid_keywords)
    if c.spicy_ok is True and m["spicy"] >= 2:
        score += 1
    return score


def passing_menus(menus: list[dict], c: UserConstraints, recent: list[str],
                  eaten: list[str]) -> list[dict]:
    """block_reason, over_limit 둘 다 통과한 메뉴를 match_score 순으로(상한 적용 전)."""
    passed = [m for m in menus
              if block_reason(m, c, recent, eaten) is None and over_limit(m, c) is None]
    passed.sort(key=lambda m: match_score(m, c), reverse=True)
    return passed


def _third_value(values: list[int]) -> int | None:
    """정렬해서 3번째 값(부족하면 마지막) — 후보가 최소 3개 생기도록."""
    values = sorted(values)
    return values[min(MAX_PICKS - 1, len(values) - 1)] if values else None


def propose_relax(menus: list[dict], c: UserConstraints, relax_count: int,
                  recent: list[str], eaten: list[str]) -> dict | None:
    """다음에 풀 조건과 새 값을 계산한다(노트북 relax 의 계산 부분). 적용은 relax 노드가 한다.

    순서: 예산 먼저(relax_count==0), 다음 시간. 노트북은 예산 차례인데 풀 값이 없으면
    아무것도 안 바꾸고 1회를 소모했다. 여기서는 그 경우 바로 시간 쪽을 제안한다.
    """
    if relax_count >= MAX_RELAX:
        return None
    safe = [m for m in menus if block_reason(m, c, recent, eaten) is None]
    field, new = None, None
    if c.budget is not None and relax_count == 0:
        new = _third_value([m["price"] for m in safe
                            if c.max_minutes is None or m["minutes"] <= c.max_minutes])
        field = "budget" if new is not None else None
    if field is None and c.max_minutes is not None:
        new = _third_value([m["minutes"] for m in safe
                            if c.budget is None or m["price"] <= c.budget])
        field = "max_minutes" if new is not None else None
    if field is None:
        return None
    old = getattr(c, field)
    relaxed_c = c.model_copy(update={field: new})
    expected = len(passing_menus(menus, relaxed_c, recent, eaten))
    return {"field": field, **_RELAX_FIELDS[field], "from": old, "to": new,
            "expected_count": expected}
