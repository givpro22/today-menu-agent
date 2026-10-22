from langsmith import traceable

from ..deps import SupervisorDeps
from ..rules import MAX_PICKS, block_reason, over_limit
from ..schemas import UserConstraints
from ..state import MenuState
from . import step

_KEPT_FIELDS = ("name", "category", "price", "minutes", "spicy", "tags", "allergens")


@traceable(name="verify_node")
async def verify_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """노트북 verify — 항상 코드가 마지막에. filter 와 같은 block_reason/over_limit 를 쓴다.
    LLM 이 무엇을 고르든 이 노드를 거치지 않고는 결과에 못 들어간다.
    카드 값(가격·시간 등)은 LLM 이 아니라 메뉴 데이터에서 복사한다.
    노트북과 달리 이번 요청의 후보 목록 밖 메뉴도 뺀다(프롬프트 규칙 1 을 코드로 강제)."""
    c = UserConstraints(**state["constraints"])
    recent, eaten = state.get("recent", []), state.get("eaten", [])
    by_name = {m["name"]: m for m in await deps.menu_repo.list_menus()}
    candidate_names = {m["name"] for m in state.get("candidates", [])}
    rec = state["recommendation"]
    kept, new_dropped = [], []
    for p in rec["picks"]:
        if any(k["name"] == p["name"] for k in kept) or len(kept) >= MAX_PICKS:
            continue   # 중복·초과분은 조건 위반이 아니라 형식 문제라 dropped 에 넣지 않는다
        m = by_name.get(p["name"])
        if m is None:
            why = "메뉴 데이터에 없는 이름"
        elif p["name"] not in candidate_names:
            why = "후보 목록 밖 메뉴"
        else:
            why = block_reason(m, c, recent, eaten) or over_limit(m, c)
        if why:
            new_dropped.append({"name": p["name"], "why": why})
        else:
            kept.append({**{k: m[k] for k in _KEPT_FIELDS},
                         "reason": p["reason"], "review_fixed": False})
    return {"kept": kept, "dropped": state.get("dropped", []) + new_dropped,
            "summary": rec.get("summary", ""),
            **step(state, "verify", "Verified",
                   kept=[k["name"] for k in kept], dropped=new_dropped)}
