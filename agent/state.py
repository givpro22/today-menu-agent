"""그래프 State — 노트북 5-5 MenuState 를 sample AgentState 관례(total=False, reducer 없음)로.

thread 가 사용자 단위로 영속되므로 요청마다 initial_state() 가 요청 범위 키를 전부
초기화한다. reducer 를 쓰면 이전 요청의 trace/events 가 계속 쌓인다(계약 D5).
pydantic 객체는 넣지 않는다(dict 만).
"""
from typing import TypedDict


class MenuState(TypedDict, total=False):
    # 요청 입력 (server 가 채움)
    query: str
    user_id: str
    history: list[dict]
    recent: list[str]
    # 노드가 채움
    route: str | None
    constraints: dict
    eaten: list[str]
    candidates: list[dict]
    relax_count: int
    relaxed: list[dict]
    proposal: dict | None
    approved: bool | None
    recommendation: dict
    attempts: int
    kept: list[dict]
    dropped: list[dict]
    review_flags: list[dict]
    summary: str
    no_result_reason: str | None
    recent_after: list[str]
    final: str
    last_prompt: str | None
    trace: list[str]
    events: list[dict]
    fallback: list[str]
    cost_usd: float
    llm_calls: int


def initial_state(query: str, user_id: str, history: list[dict],
                  recent: list[str]) -> dict:
    """계약 §4-3 의 모든 키를 채운다. recent 는 session_store 에서 읽어 넣는다(노트북 ask()
    는 체크포인터에 recent 를 이어 붙였지만, 여기서는 DB 가 원천이다)."""
    return {
        "query": query, "user_id": user_id, "history": list(history), "recent": list(recent),
        "route": None, "constraints": {}, "eaten": [], "candidates": [],
        "relax_count": 0, "relaxed": [], "proposal": None, "approved": None,
        "recommendation": {}, "attempts": 0, "kept": [], "dropped": [],
        "review_flags": [], "summary": "", "no_result_reason": None, "recent_after": [],
        "final": "", "last_prompt": None, "trace": [], "events": [], "fallback": [],
        "cost_usd": 0.0, "llm_calls": 0,
    }
