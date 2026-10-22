"""Wave 13 — 의존성 주입.

노드 함수가 closure의 지역 변수에 암묵적으로 기대지 않도록, 그래프가 쓰는 모든
의존성을 SupervisorDeps 하나에 명시적으로 담아 functools.partial로 각 노드에 주입한다.
단위 테스트에서 fake LLM, LocalMenuRepo, 임시 DB 로 갈아 끼워도 동작이 같다.
(노트북 7-1 은 전역 recommender 를 바꿔 끼웠는데, 테스트에서 교체가 안 됐다.)
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class SupervisorDeps:
    fast_llm: Any            # route, extract, review, chat — 저비용 모델
    quality_llm: Any         # recommend — 고품질 모델
    menu_repo: Any           # MenuRepo (agent/menu_repo.py). 그래프는 MCP 를 직접 모른다
    session_store: Any       # SqliteSessionStore. remember 가 save_recommendations 호출
    review_mode: str = "llm" # "off" | "rules" | "llm"
