"""최종 문장 조립 — 코드가 만든다. 완화 내역은 relaxed 를 읽어 첫 줄에 쓴다(노트북 7-1 교훈)."""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import MenuState
from . import step

KEEP_LIMITS = "알레르기, 맵기, 끼니 조건은 풀지 않았습니다."
NO_RESULT_SUMMARY = {
    "relax_rejected": "조건을 풀지 않고는 맞는 메뉴가 없어요. 예산이나 시간을 바꿔서 다시 물어봐 주세요. "
                      + KEEP_LIMITS,
    "relax_exhausted": "조건을 두 번 풀어도 맞는 메뉴를 찾지 못했어요. " + KEEP_LIMITS,
    "nothing_to_relax": "조건에 맞는 메뉴를 찾지 못했고, 더 풀 수 있는 예산·시간 조건도 없어요. "
                        + KEEP_LIMITS,
    "verify_failed": "추천 결과가 조건 검증을 통과하지 못했어요. 다시 물어봐 주세요. " + KEEP_LIMITS,
}


def relax_line(r: dict) -> str:
    return f"조건 완화: {r['label']} {r['from']:,}{r['unit']} → {r['to']:,}{r['unit']}"


@traceable(name="no_result_node")
async def no_result_node(state: MenuState, deps: SupervisorDeps) -> dict:
    """사유 결정: 앞 노드가 남긴 값(relax_rejected, nothing_to_relax) → recommend 를 탔으면
    verify_failed → 아니면 relax 상한 소진."""
    reason = state.get("no_result_reason") or (
        "verify_failed" if state.get("attempts", 0) > 0 else "relax_exhausted")
    return {"kept": [], "no_result_reason": reason, "summary": NO_RESULT_SUMMARY[reason],
            **step(state, "no_result", "NoResult", reason=reason)}


@traceable(name="finalize_node")
async def finalize_node(state: MenuState, deps: SupervisorDeps) -> dict:
    kept = state.get("kept", [])
    if not kept:
        return {"final": state.get("summary", ""),
                **step(state, "finalize", "Finalize", pipeline="menu-no-result")}
    lines = [relax_line(r) for r in state.get("relaxed", [])]
    lines += [f"{i}. {k['name']} ({k['price']:,}원, {k['minutes']}분) - {k['reason']}"
              for i, k in enumerate(kept, 1)]
    if state.get("summary"):
        lines.append(state["summary"])
    return {"final": "\n".join(lines), **step(state, "finalize", "Finalize", pipeline="menu")}
