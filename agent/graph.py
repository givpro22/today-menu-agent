"""오늘뭐먹지 StateGraph — 조립만. 노드 본문은 agent/nodes/, 판정 규칙은 agent/rules.py.

route → (chat 직행 | menu 파이프라인)
menu: extract → filter ─ 후보 있음 → recommend → verify ─ 새로 빠진 게 있고 재추천 여유 → recommend
                       │                                └ 통과 → review → remember → finalize
                       ├ 후보 0, 완화 여유 → approve_relax(interrupt) ─ 승인 → relax → filter
                       │                                             └ 거절/제안 불가 → no_result
                       └ 후보 0, 완화 상한 → no_result → finalize

노트북 그래프(5-8)와 다른 점: approve_relax(HITL), review(이유 검증), route/chat_answer/finalize
(sample 기능 유지). 루프 상한은 State 카운터(relax_count, attempts)로 둔다.
모든 노드는 SupervisorDeps를 functools.partial로 주입받는다(Wave 13).
"""
from functools import partial

from langgraph.graph import END, StateGraph

from .deps import SupervisorDeps
from .nodes.chat import chat_answer_node
from .nodes.extract import extract_node
from .nodes.filter import filter_node
from .nodes.finalize import finalize_node, no_result_node
from .nodes.recommend import recommend_node
from .nodes.relax import approve_relax_node, relax_node
from .nodes.remember import remember_node
from .nodes.review import review_node
from .nodes.route import route_node
from .nodes.verify import verify_node
from .rules import MAX_RECOMMEND_ATTEMPTS, MAX_RELAX
from .state import MenuState


def _after_route(state: MenuState) -> str:
    return "chat_answer" if state["route"] == "chat" else "extract"


def _after_filter(state: MenuState) -> str:
    if state["candidates"]:
        return "recommend"
    if state.get("relax_count", 0) < MAX_RELAX:
        return "approve_relax"
    return "no_result"


def _after_approve_relax(state: MenuState) -> str:
    return "relax" if state.get("approved") and state.get("proposal") else "no_result"


def _after_verify(state: MenuState) -> str:
    new_dropped = state["events"][-1].get("dropped", [])   # 이번 회차 Verified 이벤트
    if new_dropped and state.get("attempts", 0) < MAX_RECOMMEND_ATTEMPTS:
        return "recommend"
    if not state.get("kept"):
        return "no_result"
    return "review"


def build_graph(deps: SupervisorDeps, checkpointer=None):
    """checkpointer 가 있어야 approve_relax 의 interrupt 가 동작한다. None 이면
    interrupt 를 타지 않는 경로(chat, 후보가 바로 있는 menu) 테스트용."""
    g = StateGraph(MenuState)
    node = lambda fn: partial(fn, deps=deps)   # Wave 13 — 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("extract", node(extract_node))
    g.add_node("filter", node(filter_node))
    g.add_node("approve_relax", node(approve_relax_node))
    g.add_node("relax", node(relax_node))
    g.add_node("recommend", node(recommend_node))
    g.add_node("verify", node(verify_node))
    g.add_node("review", node(review_node))
    g.add_node("remember", node(remember_node))
    g.add_node("no_result", node(no_result_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "extract": "extract"})
    g.add_edge("chat_answer", END)
    g.add_edge("extract", "filter")
    g.add_conditional_edges("filter", _after_filter,
                            {"recommend": "recommend", "approve_relax": "approve_relax",
                             "no_result": "no_result"})
    g.add_conditional_edges("approve_relax", _after_approve_relax,
                            {"relax": "relax", "no_result": "no_result"})
    g.add_edge("relax", "filter")
    g.add_edge("recommend", "verify")
    g.add_conditional_edges("verify", _after_verify,
                            {"recommend": "recommend", "no_result": "no_result",
                             "review": "review"})
    g.add_edge("review", "remember")
    g.add_edge("remember", "finalize")
    g.add_edge("no_result", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
