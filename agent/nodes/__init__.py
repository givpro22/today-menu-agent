"""그래프 노드 — 노드마다 파일 하나. 시그니처는 sample 관례:
`async def xxx_node(state: MenuState, deps: SupervisorDeps) -> dict`, `@traceable(name="xxx_node")`.

reducer 가 없으므로(계약 D5) 리스트 키는 전체를 새로 만들어 돌려준다. 아래 helper 가
모든 노드의 공통 갱신(trace, events, LLM 비용·호출 수·폴백 메모)을 만든다.
"""
import logging

log = logging.getLogger("agent.nodes")


def step(state: dict, node: str, event_type: str, **fields) -> dict:
    """trace 에 노드 이름, events 에 이벤트 1개(노드 실행 1회당 정확히 1개)."""
    return {"trace": state.get("trace", []) + [node],
            "events": state.get("events", []) + [{"type": event_type, "node": node, **fields}]}


def llm_usage(state: dict, usage: dict) -> dict:
    """LLM 메서드 호출 1회분 누적. 파싱 재시도로 HTTP 를 두 번 쳤으면 usage["calls"]=2.
    usage["fallback"] 이 있으면 폴백 메모로 남긴다."""
    out = {"cost_usd": round(state.get("cost_usd", 0.0) + usage.get("usd", 0.0), 6),
           "llm_calls": state.get("llm_calls", 0) + usage.get("calls", 1)}
    if usage.get("fallback"):
        log.warning("LLM fallback: %s", usage["fallback"])
        out["fallback"] = state.get("fallback", []) + [usage["fallback"]]
    return out
