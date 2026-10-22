"""공통 fixture 와 안전 불변식 헬퍼.

네트워크·실제 LLM 없이 돈다: LLM 은 fake, 메뉴는 LocalMenuRepo, DB 는 임시 파일.
agent/session_store.py·meal_db.py 는 import 시점에 MENU_DB_PATH 를 읽으므로 env 를
모든 import 보다 먼저 정한다. server.py 의 load_dotenv() 는 이미 있는 env 를 덮지 않는다.
"""
import asyncio
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="today-menu-qa-"))
os.environ.update({
    "LLM_BACKEND": "fake",
    "MENU_REPO": "local",
    "CHECKPOINTER": "memory",
    "REVIEW_MODE": "llm",
    "FAKE_LLM_MODE": "normal",
    "MCP_FLAKY_FIRST_CALL": "0",
    "LANGCHAIN_TRACING_V2": "false",
    "MENU_DB_PATH": str(_TMP / "session.db"),
    "MENU_CHECKPOINT_DB": str(_TMP / "checkpoints.db"),
})
os.environ.pop("LANGCHAIN_API_KEY", None)

import pytest  # noqa: E402

ROOT = Path(__file__).parent.parent
CONTRACT = ROOT / "_workspace" / "01_architect_contracts.md"
MENUS = {m["name"]: m for m in json.loads((ROOT / "data" / "menus.json").read_text(encoding="utf-8"))}

CASE1 = "어제 술 먹고 치킨까지 먹어서 속이 안 좋아. 기름진 건 못 먹겠고 혼자 먹을 건데 만원 안쪽으로 20분 안에 되는 거 없을까"
CASE1B = "오늘도 속이 별로야. 어제처럼 만원 안쪽으로 20분 안에 먹을 수 있는 걸로 추천해 줘"
CASE2 = "친구 세 명이랑 저녁 먹는데 매운 게 당겨. 예산은 1인 15000원까지 괜찮고 시간은 넉넉해. 새우 알레르기 있는 친구가 있어"
CASE3 = "돈이 없어서 5000원 안쪽으로 먹어야 해. 근데 밀가루 알레르기 있어서 면이랑 빵은 못 먹어"
GREETING = "안녕하세요. 어떤 일을 할 수 있나요?"
MANY_ALLERGIES = "점심 추천해 줘. 밀이랑 대두랑 계란이랑 돼지고기랑 소고기랑 생선이랑 유제품이랑 새우랑 땅콩이랑 닭고기랑 메밀이랑 조개 알레르기 있어"
SEAFOOD = "해산물 알레르기 있어. 저녁 추천해 줘"
SEAFOOD_SHORT = "해산물 알레르기가 있어서 점심은 해산물 없는 걸로 추천해 줘"
WHEAT_LUNCH = "점심 추천해 줘. 밀가루 알레르기 있어"
CORIANDER = "고수 알레르기 있어. 점심 추천해 줘"

# ── 알레르기 정답표 (사람이 손으로 적은 값) ─────────────────────────────────────
# 시스템(data/allergen_alias.json, rules.normalize_allergens)을 읽지 않는다. 시스템 사전이
# 틀리면 그 사전으로 만든 기대값도 같이 틀려서 테스트가 통과해 버리기 때문이다(회차 2 F2-1).
# 값 = 메뉴 데이터(menus.json allergens) 표기로, 이 말을 한 사용자에게 절대 나오면 안 되는 재료.
# "해산물" 범위 = 리더 결정·계약 v1.1(새우·오징어·조개·생선)을 손으로 옮겨 적었다. 바뀌면 여기만 고친다.
EXPECTED_BLOCK = {
    "밀": {"밀"}, "밀가루": {"밀"}, "면": {"밀"}, "빵": {"밀"}, "글루텐": {"밀"},
    "새우": {"새우"}, "갑각류": {"새우"},
    "해산물": {"새우", "오징어", "조개", "생선"},
    "조개": {"조개"}, "생선": {"생선"},
    "대두": {"대두"}, "콩": {"대두"}, "계란": {"계란"}, "달걀": {"계란"},
    "돼지고기": {"돼지고기"}, "소고기": {"소고기"}, "닭고기": {"닭고기"},
    "유제품": {"유제품"}, "우유": {"유제품"}, "땅콩": {"땅콩"}, "메밀": {"메밀"},
    "고수": set(),   # 메뉴 데이터에 없는 재료 — 막을 것이 없다
}
UMBRELLA_ALLERGENS = {"해산물": EXPECTED_BLOCK["해산물"]}


def _block(*terms: str) -> frozenset:
    return frozenset().union(*(EXPECTED_BLOCK[t] for t in terms))


# 테스트에 쓰는 모든 문장 → 그 사용자에게 나오면 안 되는 데이터 알레르기 (손으로 적음).
# 등록 안 된 문장으로 assert_safe 를 부르면 KeyError — 정답 없이 검사가 통과하지 않게.
QUERY_FORBIDDEN = {
    CASE1: _block(), CASE1B: _block(), CASE2: _block("새우"), CASE3: _block("밀가루", "면", "빵"),
    GREETING: _block(), "제 이름은 민수입니다": _block(), "제 이름이 뭐였죠?": _block(),
    "오늘 날씨 어때?": _block(), "점심 추천": _block(), "점심 " + "먹" * 497: _block(),
    "먹" * 500: _block(),
    MANY_ALLERGIES: _block("밀", "대두", "계란", "돼지고기", "소고기", "생선", "유제품", "새우",
                           "땅콩", "닭고기", "메밀", "조개"),
    SEAFOOD: _block("해산물"), SEAFOOD_SHORT: _block("해산물"),
    WHEAT_LUNCH: _block("밀가루"), CORIANDER: _block("고수"),
}


def forbidden_for(query: str) -> frozenset:
    if query not in QUERY_FORBIDDEN:
        raise KeyError(f"QUERY_FORBIDDEN 에 정답이 없는 문장: {query[:40]}")
    return QUERY_FORBIDDEN[query]


def tmp_path_for(name: str) -> Path:
    return _TMP / name


def new_user() -> str:
    return "qa-" + uuid.uuid4().hex[:10]


def run(coro):
    return asyncio.run(coro)


# ── 계약 문서에서 예시 JSON 읽기 ─────────────────────────────────────────────
def contract_version() -> str:
    """계약 머리말의 '**계약 버전: X**'. 예시 JSON 안의 contract_version 은 작성 당시 값이라 쓰지 않는다."""
    m = re.search(r"\*\*계약 버전: ([0-9.]+)\*\*", CONTRACT.read_text(encoding="utf-8"))
    return m.group(1)


def contract_example(letter: str) -> dict:
    md = CONTRACT.read_text(encoding="utf-8")
    i = md.index(f"**({letter})")
    s = md.index("```json", i) + len("```json")
    return json.loads(md[s:md.index("```", s)])


def contract_health() -> dict:
    md = CONTRACT.read_text(encoding="utf-8")
    i = md.index("### 2-6")
    s = md.index("```json", i) + len("```json")
    return json.loads(md[s:md.index("```", s)])


# ── 안전 불변식 ─────────────────────────────────────────────────────────────
def _original_constraints(events: list[dict]) -> dict | None:
    ev = next((e for e in events if e.get("type") == "ConstraintsExtracted"), None)
    return ev["constraints"] if ev else None


def assert_safe(resp: dict, forbidden) -> None:
    """모든 경로에 거는 불변식. forbidden = 정답표에서 온 '나오면 안 되는 데이터 알레르기'
    (QUERY_FORBIDDEN / forbidden_for). 응답의 constraints.allergies 는 기준으로 쓰지 않는다.
    0) 추출 확인: 메뉴 경로면 constraints.allergies 가 정답을 모두 덮는다(정규화 누락 탐지).
    1) kept 의 어떤 메뉴도 메뉴 데이터 기준으로 forbidden 재료를 포함하지 않는다.
    2) kept 값은 메뉴 데이터 원본과 같다(LLM 이 바꾼 값이 아님).
    3) 예산·시간이 추출값과 달라졌다면 relaxed 에 그 변경이 정확히 기록돼 있다(무단 완화 없음).
    4) kept 는 최종 조건의 예산·시간·맵기·끼니를 지킨다.
    5) kept 는 서로 다른 이름 최대 3개, done 이 아니면 kept 는 비어 있다.
    """
    forbidden = set(forbidden)
    kept = resp.get("kept") or []
    names = [k["name"] for k in kept]
    assert len(names) == len(set(names)) and len(names) <= 3, names
    for k in kept:
        hit = set(MENUS[k["name"]]["allergens"]) & forbidden
        assert not hit, f"알레르기 위반 {k['name']} 에 {sorted(hit)} (막아야 할 것 {sorted(forbidden)})"
    c = resp.get("constraints")
    if c:
        missing = forbidden - set(c.get("allergies") or [])
        assert not missing, f"추출·정규화가 알레르기 {sorted(missing)} 를 놓침 (constraints.allergies={c.get('allergies')})"
    if resp.get("status") != "done":
        assert kept == [], resp.get("status")
        return
    for k in kept:
        m = MENUS[k["name"]]
        for f in ("category", "price", "minutes", "spicy", "tags", "allergens"):
            assert k[f] == m[f], f"{k['name']}.{f} 가 메뉴 데이터와 다름"
        if c.get("budget") is not None:
            assert m["price"] <= c["budget"], f"예산 초과 {k['name']}"
        if c.get("max_minutes") is not None:
            assert m["minutes"] <= c["max_minutes"], f"시간 초과 {k['name']}"
        if c.get("spicy_ok") is False:
            assert m["spicy"] < 2
        if c.get("meal") in ("점심", "저녁"):
            assert "간식" not in m["tags"]
    orig = _original_constraints(resp.get("events") or [])
    if orig is not None:
        relaxed = {r["field"]: r for r in resp.get("relaxed") or []}
        for field in ("budget", "max_minutes"):
            if orig.get(field) != c.get(field):
                r = relaxed.get(field)
                assert r is not None, f"{field} 가 relaxed 기록 없이 바뀜 {orig.get(field)} → {c.get(field)}"
                assert r["from"] == orig.get(field) and r["to"] == c.get(field), r
        for field in ("allergies", "spicy_ok", "meal"):
            assert orig.get(field) == c.get(field), f"{field} 는 풀면 안 됨"


def assert_state_safe(state: dict) -> None:
    """그래프 최종 State 용. 정답은 state["query"] 로 QUERY_FORBIDDEN 에서 찾는다."""
    menu = state.get("route") == "menu"
    resp = {"status": "done" if state.get("kept") and menu else "x",
            "kept": state.get("kept") or [] if menu else [],
            "constraints": state.get("constraints") if menu else None,
            "relaxed": state.get("relaxed") or [], "events": state.get("events") or []}
    if resp["status"] != "done":
        resp["kept"] = []
    assert_safe(resp, forbidden_for(state["query"]))


# ── 그래프 fixture ──────────────────────────────────────────────────────────
class GraphHarness:
    def __init__(self, mode: str = "normal", review_mode: str = "llm", quality=None, fast=None,
                 checkpointer=None, db_name: str | None = None):
        from langgraph.checkpoint.memory import MemorySaver

        from agent.deps import SupervisorDeps
        from agent.fake_llm import FastLLM, QualityLLM
        from agent.graph import build_graph
        from agent.menu_repo import LocalMenuRepo
        from agent.session_store import SqliteSessionStore

        db = tmp_path_for(db_name or f"g-{uuid.uuid4().hex[:8]}.db")
        self.repo = LocalMenuRepo(db_path=db)
        self.store = SqliteSessionStore(db_path=db)
        self.deps = SupervisorDeps(
            fast_llm=fast or FastLLM("fast-model-stub", 0.00015, mode=mode),
            quality_llm=quality or QualityLLM("quality-model-stub", 0.005, mode=mode),
            menu_repo=self.repo, session_store=self.store, review_mode=review_mode)
        self.graph = build_graph(self.deps, checkpointer=checkpointer if checkpointer is not None else MemorySaver())

    def config(self, user_id):
        return {"configurable": {"thread_id": user_id}}

    def ask(self, query, user_id):
        from agent.rules import RECENT_LIMIT
        from agent.state import initial_state
        recent = self.store.recent_recommendations(user_id, limit=RECENT_LIMIT)
        return run(self.graph.ainvoke(initial_state(query, user_id, self.store.load(user_id), recent),
                                      self.config(user_id)))

    def resume(self, user_id, approved):
        from langgraph.types import Command
        return run(self.graph.ainvoke(Command(resume={"approved": approved}), self.config(user_id)))

    def close(self):
        self.repo.close()
        self.store.close()


@pytest.fixture
def harness():
    made = []

    def make(**kw):
        h = GraphHarness(**kw)
        made.append(h)
        return h
    yield make
    for h in made:
        h.close()


# ── API fixture ─────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    """server.py 를 TestClient 로. 기본 CHECKPOINTER=memory. env 를 바꾸고 싶으면 make(**env)."""
    from fastapi.testclient import TestClient
    import server

    opened = []

    def make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        c = TestClient(server.app, raise_server_exceptions=False)   # 500·502 를 응답으로 받는다
        c.__enter__()
        opened.append(c)
        return c
    yield make
    for c in opened:
        c.__exit__(None, None, None)


def parse_sse(text: str) -> list:
    frames = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data: "):
            continue
        data = block[len("data: "):]
        frames.append(data if data == "[DONE]" else json.loads(data))
    return frames


def frontend_reads() -> set[str]:
    """04_frontend_fields.md 의 ChatResponse 표 첫 칸에 적힌 필드 경로."""
    md = (ROOT / "_workspace" / "04_frontend_fields.md").read_text(encoding="utf-8")
    sec = md[md.index("## ChatResponse 필드"):md.index("## 기타 응답")]
    out = set()
    for line in sec.splitlines():
        m = re.match(r"\|\s*(`[^|]+`)\s*\|", line)
        if not m:
            continue
        for tok in re.findall(r"`([^`]+)`", m.group(1)):
            base, _, rest = tok.partition(".")
            if rest:
                head = base.replace("[]", "")
                for sub in re.split(r",\s*", rest):
                    out.add(f"{head}.{sub.strip()}")
            else:
                out.add(tok)
    return out
