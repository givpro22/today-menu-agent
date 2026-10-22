"""rules.py 순수 함수 + relax 제안 + 추출 정규화 (menu-integration-qa §2 rules/relax)."""
import inspect

import pytest
from conftest import CASE1, CASE2, CASE3, MENUS, UMBRELLA_ALLERGENS, run

from agent import rules
from agent.fake_llm import rule_extract
from agent.menu_repo import LocalMenuRepo
from agent.schemas import UserConstraints

ALIAS = run(LocalMenuRepo().get_allergen_alias())
ALL = list(MENUS.values())


def C(**kw):
    return UserConstraints(**kw)


def test_allergy_blocks_jjajang():
    assert rules.block_reason(MENUS["짜장면"], C(allergies=["밀"]), []) == "알레르기 재료 포함"


def test_normalize_flour_to_wheat_and_dedup():
    assert rules.normalize_allergens(["밀가루"], ALIAS) == ["밀"]
    assert rules.normalize_allergens(["밀가루", "면", "빵", " 달걀 ", ""], ALIAS) == ["밀", "계란"]


def test_spicy_block():
    assert rules.block_reason(MENUS["김치찌개"], C(spicy_ok=False), []) == "매운 메뉴"
    assert rules.block_reason(MENUS["콩나물국밥"], C(spicy_ok=False), []) is None   # 맵기 1


def test_snack_blocked_at_lunch_and_dinner():
    for meal in ("점심", "저녁"):
        assert rules.block_reason(MENUS["떡볶이"], C(meal=meal), []) == "끼니가 아닌 간식"
    assert rules.block_reason(MENUS["떡볶이"], C(meal="간식"), []) is None


def test_recent_and_eaten_block():
    assert rules.block_reason(MENUS["된장찌개"], C(), ["된장찌개"]) == "직전에 추천한 메뉴"
    assert rules.block_reason(MENUS["된장찌개"], C(), [], ["된장찌개"]) == "최근에 먹은 메뉴"


def test_over_limit_budget_and_time():
    assert rules.over_limit(MENUS["삼계탕"], C(budget=10000)) == "예산 초과 17000원"
    assert rules.over_limit(MENUS["삼계탕"], C(max_minutes=20)) == "시간 초과 25분"
    assert rules.over_limit(MENUS["김밥"], C(budget=5000, max_minutes=5)) is None


def test_relax_case3_proposes_budget_8500():
    c = UserConstraints(**rule_extract(CASE3))
    c = c.model_copy(update={"allergies": rules.normalize_allergens(c.allergies, ALIAS)})
    assert c.budget == 5000 and c.allergies == ["밀"]
    assert rules.passing_menus(ALL, c, [], []) == []
    p = rules.propose_relax(ALL, c, 0, [], [])
    assert p == {"field": "budget", "label": "예산", "unit": "원", "from": 5000, "to": 8500,
                 "expected_count": 3}


def test_relax_skips_to_time_when_budget_has_no_value():
    # 시간 3분 안에 되는 메뉴가 없으면 예산 제안이 불가 → 바로 시간 제안 (agent (e))
    c = C(budget=5000, max_minutes=3)
    p = rules.propose_relax(ALL, c, 0, [], [])
    assert p["field"] == "max_minutes" and p["from"] == 3 and p["to"] >= 5
    assert p["expected_count"] >= 1


def test_relax_never_touches_allergy_and_caps():
    c = C(allergies=[a for m in ALL for a in m["allergens"]], budget=1000)
    assert rules.propose_relax(ALL, c, 0, [], []) is None          # 알레르기로 전부 막힘 → 제안 불가
    assert rules.propose_relax(ALL, C(budget=1000), rules.MAX_RELAX, [], []) is None


def test_relax_proposal_yields_candidates_after_apply():
    for budget, minutes in [(5000, None), (3000, 5), (None, 3), (6000, 4)]:
        c = C(budget=budget, max_minutes=minutes, allergies=["밀"])
        if rules.passing_menus(ALL, c, [], []):
            continue
        p = rules.propose_relax(ALL, c, 0, [], [])
        if p is None:   # 예산·시간 어느 쪽으로도 안전한 메뉴가 없으면 nothing_to_relax
            continue
        relaxed = c.model_copy(update={p["field"]: p["to"]})
        assert len(rules.passing_menus(ALL, relaxed, [], [])) == p["expected_count"] >= 1


def test_extract_minimums_contract_5_3():
    c1 = rule_extract(CASE1)
    assert c1["budget"] == 10000 and c1["max_minutes"] == 20 and c1["allergies"] == [] and "기름진" in c1["avoid_keywords"]
    c2 = rule_extract(CASE2)
    assert (c2["budget"], c2["spicy_ok"], c2["allergies"], c2["meal"], c2["people"]) == (15000, True, ["새우"], "저녁", 4)


def test_filter_and_verify_share_rule_functions():
    """복붙 판정 함수 금지: filter·verify·relax 가 rules 의 같은 함수 객체를 쓴다."""
    from agent.nodes import filter as f_mod, relax as r_mod, verify as v_mod
    assert v_mod.block_reason is rules.block_reason and v_mod.over_limit is rules.over_limit
    assert f_mod.passing_menus is rules.passing_menus and r_mod.propose_relax is rules.propose_relax
    for mod in (f_mod, v_mod, r_mod):
        src = inspect.getsource(mod)
        assert "def block_reason" not in src and "def over_limit" not in src


def test_alias_values_exist_in_menu_allergens():
    """v1.1: alias 값은 str 또는 list[str]. 어느 쪽이든 메뉴 데이터에 있는 표기만."""
    data_allergens = {a for m in ALL for a in m["allergens"]}
    values = {x for v in ALIAS.values() for x in (v if isinstance(v, list) else [v])}
    assert values <= data_allergens, values - data_allergens


def test_normalize_matches_hand_written_truth():
    """시스템 정규화 결과 ⊇ 손으로 적은 정답(conftest.EXPECTED_BLOCK). 정답은 alias 를 읽지 않는다."""
    from conftest import EXPECTED_BLOCK
    for term, must in EXPECTED_BLOCK.items():
        got = set(rules.normalize_allergens([term], ALIAS))
        assert must <= got, f"{term}: 정규화 {sorted(got)} 가 정답 {sorted(must)} 를 덮지 못함"


@pytest.mark.parametrize("term,members", sorted(UMBRELLA_ALLERGENS.items()))
def test_umbrella_allergy_terms_cover_all_members(term, members):
    """포괄 표기('해산물')는 그 재료 전부를 막아야 한다. 기준 집합은 conftest.UMBRELLA_ALLERGENS."""
    c = C(allergies=rules.normalize_allergens([term], ALIAS))
    leaked = [m["name"] for m in ALL if rules.block_reason(m, c, []) is None
              and set(m["allergens"]) & members]
    assert leaked == [], f"{term} 알레르기인데 통과: {leaked}"
