"""그래프 경로·기억·HITL·verify·review (menu-integration-qa §2 graph/기억). 모든 경로에 assert_state_safe."""
from conftest import (CASE1, CASE1B, CASE2, CASE3, GREETING, MANY_ALLERGIES, MENUS, UMBRELLA_ALLERGENS,
                      assert_state_safe, new_user, run, tmp_path_for)

from agent import rules
from agent.fake_llm import FastLLM, QualityLLM, template_reason


def names(state):
    return [k["name"] for k in state.get("kept", [])]


def test_case1_done(harness):
    h = harness()
    s = h.ask(CASE1, new_user())
    assert s["route"] == "menu" and "__interrupt__" not in s
    assert names(s) == ["된장찌개", "북엇국", "콩나물국밥"]
    assert s["trace"] == ["route", "extract", "filter", "recommend", "verify", "review", "remember", "finalize"]
    assert len(s["events"]) == len(s["trace"])          # 노드 1회당 이벤트 정확히 1개
    assert [e["node"] for e in s["events"]] == s["trace"]
    assert s["llm_calls"] == 4
    assert_state_safe(s)


def test_case2_no_shrimp_and_spicy(harness):
    h = harness()
    s = h.ask(CASE2, new_user())
    assert s["constraints"]["allergies"] == ["새우"] and s["constraints"]["meal"] == "저녁"
    assert names(s) and all("새우" not in MENUS[n]["allergens"] for n in names(s))
    assert_state_safe(s)


def test_case3_interrupt_approve(harness):
    h, u = harness(), new_user()
    s = h.ask(CASE3, u)
    intr = s["__interrupt__"][0].value
    assert intr == {"type": "relax_approval", "node": "approve_relax",
                    "proposal": {"field": "budget", "label": "예산", "unit": "원", "from": 5000, "to": 8500, "expected_count": 3},
                    "relax_count": 0,
                    "message": "예산 5,000원 안에서는 조건에 맞는 메뉴가 없어요. 예산을 8,500원으로 올려서 찾아볼까요?"}
    assert s["trace"] == ["route", "extract", "filter"]
    s2 = h.resume(u, True)
    assert names(s2) == ["된장찌개", "콩나물국밥", "볶음밥"]
    assert s2["relaxed"] == [{"field": "budget", "label": "예산", "unit": "원", "from": 5000, "to": 8500}]
    assert s2["final"].splitlines()[0] == "조건 완화: 예산 5,000원 → 8,500원"
    assert s2["trace"] == ["route", "extract", "filter", "approve_relax", "relax", "filter",
                           "recommend", "verify", "review", "remember", "finalize"]
    assert_state_safe(s2)


def test_case3_interrupt_reject(harness):
    h, u = harness(), new_user()
    h.ask(CASE3, u)
    s = h.resume(u, False)
    assert s["kept"] == [] and s["no_result_reason"] == "relax_rejected"
    assert s["trace"] == ["route", "extract", "filter", "approve_relax", "no_result", "finalize"]
    assert s["constraints"]["budget"] == 5000 and s["relaxed"] == []
    assert_state_safe(s)


def test_interrupt_has_no_side_effects_before_resume(harness):
    """approve_relax 앞에서는 DB 쓰기가 없어야 한다 — interrupt 시점에 recommendations 0건."""
    h, u = harness(), new_user()
    h.ask(CASE3, u)
    assert h.store.recent_recommendations(u) == []


def test_chat_route_and_name_memory(harness):
    h, u = harness(), new_user()
    s = h.ask(GREETING, u)
    assert s["route"] == "chat" and s["trace"] == ["route", "chat_answer"] and s["kept"] == []
    h.store.append(u, "user", "제 이름은 민수입니다")
    h.store.append(u, "assistant", "안녕하세요")
    s = h.ask("제 이름이 뭐였죠?", u)
    assert "민수" in s["final"]


def test_many_allergies_nothing_to_relax(harness):
    h = harness()
    s = h.ask(MANY_ALLERGIES, new_user())
    assert "__interrupt__" not in s
    assert s["no_result_reason"] == "nothing_to_relax" and s["kept"] == []
    ev = [e for e in s["events"] if e["type"] == "RelaxDecision"][0]
    assert ev["approved"] is None and ev["proposal"] is None   # agent (a): 묻지 않았음
    assert_state_safe(s)


def test_off_menu_recommend_twice(harness):
    h = harness(mode="off_menu")
    s = h.ask(CASE1, new_user())
    assert s["trace"].count("recommend") == 2
    assert {"name": "없는메뉴", "why": "메뉴 데이터에 없는 이름"} in s["dropped"]
    assert "없는메뉴" not in names(s) and names(s)
    assert_state_safe(s)


def test_bad_reason_review_flags(harness):
    h = harness(mode="bad_reason")
    s = h.ask(CASE1, new_user())
    flags = s["review_flags"]
    assert flags and flags[0]["name"] == "북엇국" and flags[0]["source"] == "rules"
    k = next(k for k in s["kept"] if k["name"] == "북엇국")
    assert k["review_fixed"] is True and "매운" not in k["reason"]
    assert_state_safe(s)


def test_review_off_and_rules_modes(harness):
    off = harness(mode="bad_reason", review_mode="off").ask(CASE1, new_user())
    assert off["review_flags"] == [] and off["llm_calls"] == 3
    rl = harness(mode="bad_reason", review_mode="rules").ask(CASE1, new_user())
    assert rl["review_flags"] and rl["llm_calls"] == 3


# ── 기억 ────────────────────────────────────────────────────────────────────
def test_same_user_twice_excludes_previous(harness):
    h, u = harness(), new_user()
    first = names(h.ask(CASE1, u))
    second = h.ask(CASE1B, u)
    assert set(first).isdisjoint(names(second)) and names(second)
    assert second["recent"] == first
    assert_state_safe(second)


def test_memory_survives_restart_with_new_db_and_checkpointer(harness):
    """같은 DB 파일, 새 SqliteSessionStore·새 MemorySaver(= 재시작) 에서도 직전 추천 제외."""
    db = f"restart-{new_user()}.db"
    u = new_user()
    first = names(harness(db_name=db).ask(CASE1, u))
    second = harness(db_name=db).ask(CASE1B, u)
    assert set(first).isdisjoint(names(second))


def test_pending_interrupt_survives_restart_sqlite(harness):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    path = str(tmp_path_for(f"ckpt-{new_user()}.db"))
    u, db = new_user(), f"rs-{new_user()}.db"

    async def first():
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            h = harness(checkpointer=saver, db_name=db)
            from agent.state import initial_state
            return await h.graph.ainvoke(initial_state(CASE3, u, [], []), h.config(u))

    async def second():
        from langgraph.types import Command
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            h = harness(checkpointer=saver, db_name=db)
            snap = await h.graph.aget_state(h.config(u))
            assert snap.interrupts and snap.interrupts[0].value["proposal"]["to"] == 8500
            return await h.graph.ainvoke(Command(resume={"approved": True}), h.config(u))

    assert "__interrupt__" in run(first())
    s = run(second())
    assert names(s) == ["된장찌개", "콩나물국밥", "볶음밥"]
    assert_state_safe(s)


def test_eaten_menu_excluded(harness):
    h, u = harness(), new_user()
    run(h.repo.save_meal(u, "된장찌개"))
    run(h.repo.save_meal(u, "된장찌개"))
    s = h.ask(CASE1, u)
    assert "된장찌개" not in names(s)
    assert s["eaten"] == ["된장찌개"]          # 같은 메뉴 두 번 먹어도 이름은 한 번
    ev = [e for e in s["events"] if e["type"] == "CandidatesFiltered"][0]
    assert ev["excluded_eaten"] == 1


# ── verify 강화 / LLM 이상 응답 (stub quality LLM) ─────────────────────────
class StubQuality(QualityLLM):
    """후보와 무관하게 정해진 picks 를 내는 quality LLM. calls 로 usage 키 실험도 한다."""

    def __init__(self, picks_by_attempt, usage_extra=None):
        super().__init__("stub-quality", 0.005)
        self.picks_by_attempt = picks_by_attempt
        self.n = 0
        self.usage_extra = usage_extra or {}
        self.seen_candidates = []

    def recommend(self, query, constraints, relaxed, candidates):
        self.seen_candidates.append([c["name"] for c in candidates])
        picks = self.picks_by_attempt[min(self.n, len(self.picks_by_attempt) - 1)]
        self.n += 1
        picks = [{"name": n, "reason": template_reason(MENUS[n]) if n in MENUS else "?"} for n in picks]
        return {"picks": picks, "summary": "요약"}, {"model": self.name, "tokens": 1, "usd": 0.0, **self.usage_extra}


def _case1_outside_candidates():
    from agent.schemas import UserConstraints
    from agent.fake_llm import rule_extract
    c = UserConstraints(**rule_extract(CASE1))
    passing = rules.passing_menus(list(MENUS.values()), c, [], [])
    return [m["name"] for m in passing[:rules.CANDIDATE_LIMIT]], [m["name"] for m in passing[rules.CANDIDATE_LIMIT:]]


def test_verify_drops_pick_outside_candidates(harness):
    """리더 결정: 조건은 지키지만 이번 후보 목록 밖인 메뉴는 dropped → 재추천."""
    inside, outside = _case1_outside_candidates()
    assert outside, "case1 에 후보 10개 밖 통과 메뉴가 있어야 이 테스트가 의미 있음"
    q = StubQuality([[outside[0], inside[0]], inside[:3]])
    h = harness(quality=q)
    s = h.ask(CASE1, new_user())
    assert {"name": outside[0], "why": "후보 목록 밖 메뉴"} in s["dropped"]
    assert outside[0] not in names(s)
    assert s["trace"].count("recommend") == 2
    assert_state_safe(s)


def test_verify_failed_when_all_picks_bad(harness):
    """agent (b): 두 번 다 조건 위반이면 kept 0 → no_result verify_failed (review 로 가지 않음)."""
    q = StubQuality([["짜장면", "우동"]])     # 밀 알레르기 위반
    h = harness(quality=q)
    s = h.ask("점심 추천해 줘. 밀가루 알레르기 있어", new_user())
    assert s["kept"] == [] and s["no_result_reason"] == "verify_failed"
    assert "review" not in s["trace"] and s["trace"][-2:] == ["no_result", "finalize"]
    assert s["trace"].count("recommend") == 2
    assert_state_safe(s)


def test_empty_picks_goes_no_result_without_retry(harness):
    q = StubQuality([[]])
    s = harness(quality=q).ask(CASE1, new_user())
    assert s["no_result_reason"] == "verify_failed" and s["trace"].count("recommend") == 1


def test_pick_dedup_and_cap_three(harness):
    """agent (d): 중복 pick 은 건너뛰고 3개에서 자른다. 둘 다 dropped 에 넣지 않는다."""
    inside, _ = _case1_outside_candidates()
    q = StubQuality([[inside[0], inside[0], inside[1], inside[2], inside[3]]])
    s = harness(quality=q).ask(CASE1, new_user())
    assert names(s) == [inside[0], inside[1], inside[2]] and s["dropped"] == []
    assert s["trace"].count("recommend") == 1


def test_retry_excludes_dropped_from_candidates(harness):
    inside, outside = _case1_outside_candidates()
    q = StubQuality([[outside[0]], inside[:3]])
    harness(quality=q).ask(CASE1, new_user())
    assert outside[0] not in q.seen_candidates[1]


def test_usage_calls_key_counts_http_calls(harness):
    """agent (c): usage["calls"]=2 (파싱 재시도) 면 llm_calls 에 2로 집계, fallback 메모는 state.fallback 에."""
    inside, _ = _case1_outside_candidates()
    q = StubQuality([inside[:3]], usage_extra={"calls": 2, "fallback": "recommend: 테스트 폴백"})
    s = harness(quality=q).ask(CASE1, new_user())
    assert s["llm_calls"] == 5             # route1 + extract1 + recommend2 + review1
    assert "recommend: 테스트 폴백" in s["fallback"]


def test_extract_schema_failure_falls_back_to_rules(harness):
    class BadFast(FastLLM):
        def extract(self, query):
            return {"budget": "만원", "allergies": "밀"}, {"model": self.name, "tokens": 1, "usd": 0.0}
    s = harness(fast=BadFast("bad-fast", 0.00015)).ask(CASE3, new_user())
    assert any("extract: 스키마 검증 실패" in f for f in s["fallback"])
    assert s["constraints"]["allergies"] == ["밀"]
    ev = s["events"][1]
    assert ev["type"] == "ConstraintsExtracted" and ev["fallback"] is True


def test_unknown_allergy_term_kept_verbatim(harness):
    s = harness().ask("고수 알레르기 있어. 점심 추천해 줘", new_user())
    assert s["constraints"]["allergies"] == ["고수"] and names(s)
    assert_state_safe(s)


def test_seafood_allergy_end_to_end(harness):
    """'해산물 알레르기' 문장에서 해산물 재료 메뉴가 추천에 나오지 않는다(기준 = conftest.UMBRELLA_ALLERGENS)."""
    q = StubQuality([["순두부찌개", "된장찌개"]])
    s = harness(quality=q).ask("해산물 알레르기 있어. 저녁 추천해 줘", new_user())
    leaked = [n for n in names(s) if set(MENUS[n]["allergens"]) & UMBRELLA_ALLERGENS["해산물"]]
    assert leaked == [], f"해산물 알레르기 사용자에게 추천됨: {leaked} (constraints.allergies={s['constraints']['allergies']})"
    assert_state_safe(s)


def test_seafood_candidates_contain_no_seafood(harness):
    """LLM 과 무관하게 filter 단계 후보부터 해산물 재료가 없어야 한다(정답표 기준, fake 기본 모드)."""
    from conftest import SEAFOOD, SEAFOOD_SHORT, forbidden_for
    for q in (SEAFOOD, SEAFOOD_SHORT):
        s = harness().ask(q, new_user())
        bad = [m["name"] for m in s["candidates"] if set(m["allergens"]) & forbidden_for(q)]
        assert bad == [], f"{q[:12]}… 후보에 해산물 재료: {bad}"
        assert_state_safe(s)
