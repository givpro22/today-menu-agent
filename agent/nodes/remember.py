from langsmith import traceable

from ..deps import SupervisorDeps
from ..rules import RECENT_LIMIT
from ..state import MenuState
from . import step


@traceable(name="remember_node")
async def remember_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """노트북 remember — 최근 추천 6개. 노트북은 MemorySaver State 에 두어 재시작하면 사라졌다.
    여기서는 session.db recommendations 테이블이 원천이다(계약 D4). 계속 쌓으면 후보가
    바닥나므로 다음 요청은 최근 RECENT_LIMIT 개만 읽는다."""
    names = [k["name"] for k in state.get("kept", [])]
    deps.session_store.save_recommendations(state["user_id"], names)
    recent_after = (state.get("recent", []) + names)[-RECENT_LIMIT:]
    return {"recent_after": recent_after,
            **step(state, "remember", "Remembered", recent=recent_after)}
