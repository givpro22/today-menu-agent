"""조건 완화 — 노트북 relax 를 "제안(approve_relax) + 적용(relax)" 둘로 나누고 그 사이에
사용자 승인(interrupt)을 넣었다. 노트북은 묻지 않고 예산을 올렸다(10절 약점 1).
"""
from langgraph.types import interrupt
from langsmith import traceable

from ..deps import SupervisorDeps
from ..rules import propose_relax
from ..schemas import UserConstraints
from ..state import MenuState
from . import step

_VERB = {"budget": "올려서", "max_minutes": "늘려서"}


def proposal_message(p: dict) -> str:
    return (f"{p['label']} {p['from']:,}{p['unit']} 안에서는 조건에 맞는 메뉴가 없어요. "
            f"{p['label']}을 {p['to']:,}{p['unit']}으로 {_VERB[p['field']]} 찾아볼까요?")


@traceable(name="approve_relax_node")
async def approve_relax_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """제안을 먼저 계산해 interrupt 로 내보낸다 — "얼마로" 를 보여 줘야 승인 의미가 있다.

    resume 하면 이 노드가 처음부터 다시 실행된다. 그래서 interrupt 앞에는 읽기만 둔다
    (DB 쓰기·LLM 호출 금지). 계산이 결정적이라 재실행해도 같은 제안이 나온다."""
    c = UserConstraints(**state["constraints"])
    menus = await deps.menu_repo.list_menus()
    relax_count = state.get("relax_count", 0)
    proposal = propose_relax(menus, c, relax_count, state.get("recent", []),
                             state.get("eaten", []))
    if proposal is None:
        return {"proposal": None, "approved": None, "no_result_reason": "nothing_to_relax",
                **step(state, "approve_relax", "RelaxDecision", approved=None, proposal=None)}

    decision = interrupt({"type": "relax_approval", "node": "approve_relax",
                          "proposal": proposal, "relax_count": relax_count,
                          "message": proposal_message(proposal)})
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    return {"proposal": proposal, "approved": approved,
            "no_result_reason": None if approved else "relax_rejected",
            **step(state, "approve_relax", "RelaxDecision", approved=approved, proposal=proposal)}


@traceable(name="relax_node")
async def relax_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """승인된 proposal 을 적용만 한다(재계산 금지). 무엇을 얼마로 풀었는지 relaxed 에 남긴다 —
    완화 문장은 finalize 가 이 목록으로 만든다(노트북 7-1: LLM 이 "풀었다" 고 거짓으로 썼다)."""
    p = state["proposal"]
    constraints = {**state["constraints"], p["field"]: p["to"]}
    applied = {k: p[k] for k in ("field", "label", "unit", "from", "to")}
    return {"constraints": constraints, "relaxed": state.get("relaxed", []) + [applied],
            "relax_count": state.get("relax_count", 0) + 1, "proposal": None,
            **step(state, "relax", "RelaxApplied", field=p["field"], **{"from": p["from"], "to": p["to"]})}
