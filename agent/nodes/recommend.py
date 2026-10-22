from langsmith import traceable
from pydantic import ValidationError

from ..deps import SupervisorDeps
from ..schemas import Recommendation
from ..state import MenuState
from . import llm_usage, log, step


@traceable(name="recommend_node")
async def recommend_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """노트북 recommend — 후보 안에서 고르고 이유 쓰기는 LLM. 재추천이면 verify 가 뺀
    이름을 후보에서 지운다. 입력에 완화 내역(relaxed)을 다시 넣었다(노트북 10절)."""
    banned = {d["name"] for d in state.get("dropped", [])}
    cands = [m for m in state["candidates"] if m["name"] not in banned]
    raw, usage = deps.quality_llm.recommend(
        state["query"], state["constraints"], state.get("relaxed", []), cands)
    updates = llm_usage(state, usage)
    try:
        rec = Recommendation(**raw).model_dump()
    except (ValidationError, TypeError) as e:
        note = f"recommend: 스키마 검증 실패 → 빈 추천 ({type(e).__name__})"
        log.warning(note)
        rec = {"picks": [], "summary": ""}
        updates["fallback"] = updates.get("fallback", state.get("fallback", [])) + [note]
    attempt = state.get("attempts", 0) + 1
    return {"recommendation": rec, "attempts": attempt, "last_prompt": usage.get("prompt"),
            **updates,
            **step(state, "recommend", "Recommended", attempt=attempt,
                   picks=[p["name"] for p in rec["picks"]], candidate_count=len(cands))}
