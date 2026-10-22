"""이유 검증(reviewer) — 이번 프로젝트 신규. 노트북 10절: 제육볶음(맵기 2)을 "매운맛 강함" 처럼
데이터와 다른 속성으로 설명해도 아무도 잡지 않았다.

1단계 코드 규칙(싸고 결정적) → 2단계 LLM-as-judge(규칙에 안 걸린 나머지만).
모순이면 이유를 데이터 기반 템플릿 문장으로 바꾸고 review_flags 에 남긴다. 메뉴는 빼지 않는다 —
안전(알레르기 등)은 verify 가 이미 처리했다. REVIEW_MODE=off|rules|llm 으로 비용·지연을 비교한다.
"""
import re

from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import MenuState
from . import llm_usage, step

_SPICY_NEG = re.compile(r"안 ?매|안 ?맵|맵지 ?않|매운 ?(맛|기)?(이|은|는)? ?(없|적)")
_SPICY_POS = re.compile(r"매운|맵|매콤|얼큰")


def rule_issue(reason: str, menu: dict) -> str | None:
    """이유 문장 속 속성이 메뉴 데이터와 모순되면 그 설명을, 아니면 None."""
    spicy = menu["spicy"]
    if _SPICY_NEG.search(reason):
        if spicy >= 2:
            return f"이유에 맵지 않다는 말이 있지만 맵기 {spicy}"
    elif (m := _SPICY_POS.search(reason)) and spicy < 2:
        return f"이유에 '{m.group(0)}'이 있지만 맵기 {spicy}"
    if "국물" in reason and "국물" not in menu["tags"]:
        return "이유에 '국물'이 있지만 국물 메뉴가 아님"
    return None


def fixed_reason(menu: dict) -> str:
    return f"{', '.join(menu['tags'][:2])} 특징이 있는 {menu['category']} 메뉴예요."


@traceable(name="review_node")
async def review_node(state: MenuState, deps: SupervisorDeps) -> dict:
    mode = deps.review_mode
    kept = [dict(k) for k in state.get("kept", [])]
    flags = list(state.get("review_flags", []))
    updates: dict = {}
    if mode in ("rules", "llm"):
        issues: dict[str, tuple[str, str]] = {}   # name → (source, issue)
        for k in kept:
            issue = rule_issue(k["reason"], k)
            if issue:
                issues[k["name"]] = ("rules", issue)
        rest = [k for k in kept if k["name"] not in issues]
        if mode == "llm" and rest:
            by_name = {m["name"]: m for m in await deps.menu_repo.list_menus()}
            items = [{"name": k["name"], "reason": k["reason"], "menu": by_name[k["name"]]}
                     for k in rest]
            result, usage = deps.fast_llm.review(items)
            updates.update(llm_usage(state, usage))
            names = {k["name"] for k in rest}
            for v in result.get("verdicts", []):
                if v.get("name") in names and v.get("contradiction"):
                    issues[v["name"]] = ("llm", v.get("evidence") or "LLM 판정: 데이터와 모순")
        for k in kept:
            if k["name"] in issues:
                source, issue = issues[k["name"]]
                new = fixed_reason(k)
                flags.append({"name": k["name"], "source": source, "issue": issue,
                              "original_reason": k["reason"], "fixed_reason": new})
                k["reason"], k["review_fixed"] = new, True
    flagged = [f["name"] for f in flags]
    return {"kept": kept, "review_flags": flags, **updates,
            **step(state, "review", "Reviewed", mode=mode, flagged=flagged)}
