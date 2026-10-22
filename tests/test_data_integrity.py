"""메뉴 데이터 무결성 — main 재료가 allergens 에 반영됐는지(손으로 적은 conftest.INGREDIENT_ALLERGENS 기준).
채점·판정·다른 테스트가 모두 menus.json 의 allergens 를 믿기 때문에, 데이터가 틀리면 전부 같이 틀린다."""
import pytest
from conftest import INGREDIENT_ALLERGENS, MENUS


@pytest.mark.parametrize("name", sorted(MENUS))
def test_main_ingredients_reflected_in_allergens(name):
    m = MENUS[name]
    missing = {ing: sorted(need - set(m["allergens"]))
               for ing in m["main"] if (need := INGREDIENT_ALLERGENS.get(ing)) and not need <= set(m["allergens"])}
    assert not missing, f"{name}: 재료→빠진 알레르기 {missing} (allergens={m['allergens']})"


def test_bukeoguk_has_fish():
    """회차 4 실측 버그 고정: 북엇국(북어) → 생선."""
    assert "생선" in MENUS["북엇국"]["allergens"]


def test_table_covers_a_meaningful_share():
    """대응표가 너무 비면 이 검사가 형식만 남는다 — 메뉴 대부분이 표에 있는 재료를 하나 이상 가진다."""
    covered = [n for n, m in MENUS.items() if any(i in INGREDIENT_ALLERGENS for i in m["main"])]
    assert len(covered) >= 30, len(covered)
