from langsmith import traceable
from pydantic import ValidationError

from ..deps import SupervisorDeps
from ..fake_llm import rule_extract
from ..rules import normalize_allergens
from ..schemas import UserConstraints
from ..state import MenuState
from . import llm_usage, log, step


@traceable(name="extract_node")
async def extract_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """노트북 extract — 문장 → 조건 객체는 LLM, 표기 정규화는 코드(data 의 alias 사전)."""
    raw, usage = deps.fast_llm.extract(state["query"])
    updates = llm_usage(state, usage)
    used_fallback = bool(usage.get("fallback"))
    try:
        c = UserConstraints(**raw)
    except (ValidationError, TypeError) as e:
        # 필드 타입이 깨진 응답(예: budget="만원") — 규칙 기반 추출로 폴백하고 기록한다
        note = f"extract: 스키마 검증 실패 → 규칙 기반 추출 ({type(e).__name__})"
        log.warning(note)
        c = UserConstraints(**rule_extract(state["query"]))
        updates["fallback"] = updates.get("fallback", state.get("fallback", [])) + [note]
        used_fallback = True
    alias = await deps.menu_repo.get_allergen_alias()
    c = c.model_copy(update={"allergies": normalize_allergens(c.allergies, alias)})
    constraints = c.model_dump()
    return {"constraints": constraints, **updates,
            **step(state, "extract", "ConstraintsExtracted",
                   constraints=constraints, fallback=used_fallback)}
