from langsmith import traceable

from ..deps import SupervisorDeps
from ..rules import CANDIDATE_LIMIT, MEAL_EXCLUDE_DAYS, passing_menus
from ..schemas import UserConstraints
from ..state import MenuState
from . import step


@traceable(name="filter_node")
async def filter_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """노트북 filter_menus — 판정은 코드. 메뉴는 menu_repo(MCP), 먹은 메뉴는 식사 기록에서."""
    c = UserConstraints(**state["constraints"])
    recent = state.get("recent", [])
    menus = await deps.menu_repo.list_menus()
    history = await deps.menu_repo.get_meal_history(state["user_id"], days=MEAL_EXCLUDE_DAYS)
    eaten = list(dict.fromkeys(h["menu_name"] for h in history))
    candidates = passing_menus(menus, c, recent, eaten)[:CANDIDATE_LIMIT]
    names = {m["name"] for m in menus}
    return {"candidates": candidates, "eaten": eaten,
            **step(state, "filter", "CandidatesFiltered",
                   count=len(candidates), relax_count=state.get("relax_count", 0),
                   excluded_recent=len(names.intersection(recent)),
                   excluded_eaten=len(names.intersection(eaten)))}
