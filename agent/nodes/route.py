from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import MenuState
from . import llm_usage, step


@traceable(name="route_node")
async def route_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """sample Wave 5 — 인사·잡담은 메뉴 파이프라인을 타지 않는다. 라벨만 incident → menu."""
    result, usage = deps.fast_llm.route(state["query"])
    route = result.get("route") if result.get("route") in ("menu", "chat") else "chat"
    return {"route": route, **llm_usage(state, usage),
            **step(state, "route", "RouteDecided", route=route)}
