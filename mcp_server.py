"""오늘뭐먹지 — MCP Server (Wave 2 축).

FastAPI 백엔드(server.py)가 lifespan에서 이 파일을 subprocess(stdio)로 1회 기동해
프로세스가 살아 있는 동안 재사용한다 (Wave 1 싱글턴 + Wave 2 표준화의 결합).
그래프가 실제로 부르는 capability 만 노출한다(계약 §6-2).

- Tool     list_menus / get_menu : data/menus.json 조회 (매 호출 파일을 읽어 데이터 교체가 바로 반영)
- Tool     get_meal_history / save_meal : session.db meals 테이블 (호출마다 값이 달라지는 동적 조회)
- Resource menu://allergens : 알레르기 표기 사전(URI로 식별되는 정적 문서). 값은 str 또는 list[str]

모든 Tool 은 JSON 객체 1개를 반환한다 — mcp_client.call_tool 이 content[0].text 하나만 파싱한다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))   # subprocess 로 뜰 때도 agent 패키지를 import 하기 위해

from fastmcp import FastMCP  # noqa: E402

from agent import meal_db  # noqa: E402

mcp = FastMCP(name="today-menu")

DATA_DIR = ROOT / "data"
_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = meal_db.connect(meal_db.DB_PATH)
    return _conn


def _menus() -> list[dict]:
    return json.loads((DATA_DIR / "menus.json").read_text(encoding="utf-8"))


def _find(name: str) -> dict | None:
    return next((m for m in _menus() if m["name"] == name), None)


@mcp.tool()
def list_menus(category: str | None = None) -> dict:
    """전체 메뉴 목록을 반환합니다. category 를 주면 그 카테고리만."""
    menus = [m for m in _menus() if category is None or m["category"] == category]
    return {"menus": menus, "count": len(menus)}


@mcp.tool()
def get_menu(name: str) -> dict:
    """이름이 정확히 같은 메뉴 1개를 반환합니다. 없으면 menu 가 null."""
    return {"menu": _find(name)}


@mcp.tool()
def get_meal_history(user_id: str, days: int = 3) -> dict:
    """user_id 가 최근 days 일 동안 먹었다고 기록한 메뉴(최신순)."""
    return {"user_id": user_id, "days": days, "items": meal_db.select_meals(_db(), user_id, days)}


@mcp.tool()
def save_meal(user_id: str, menu_name: str) -> dict:
    """'먹었어요' 기록. 메뉴 데이터에 없는 이름이면 ok=false."""
    if _find(menu_name) is None:
        return {"ok": False, "error": {"code": "UNKNOWN_MENU",
                                       "message": f"메뉴 목록에 없는 이름입니다: {menu_name}"}}
    return {"ok": True, "item": meal_db.insert_meal(_db(), user_id, menu_name)}


@mcp.resource("menu://allergens", mime_type="application/json")
def allergen_alias() -> str:
    """알레르기 표기 사전(사용자 표기 → 데이터 표기 1개 또는 리스트). 추출 직후 정규화에 쓴다."""
    return (DATA_DIR / "allergen_alias.json").read_text(encoding="utf-8")


if __name__ == "__main__":
    try:
        mcp.run(show_banner=False)
    except TypeError:   # 구버전 fastmcp 호환
        mcp.run()
