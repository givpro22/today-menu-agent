"""MenuRepo — 그래프가 아는 유일한 데이터 인터페이스 (계약 §6-3).

그래프 노드는 MCP 를 직접 모르고 deps.menu_repo 의 메서드만 부른다. 구현은 두 개다.
- LocalMenuRepo: data/*.json + agent/meal_db.py 를 직접 읽는다. pytest·MENU_REPO=local 용.
- McpMenuRepo:   mcp_server.py 의 Tool/Resource 를 with_retry 로 부르고, 실패하면
                 LocalMenuRepo 로 폴백한다. 폴백·재시도 사실은 drain_* 로 응답 metadata 에 남긴다.
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from agent import meal_db
from agent.retry import with_retry

DATA_DIR = Path(__file__).parent.parent / "data"
MCP_SERVER = "menu"
MCP_TIMEOUT = 5.0   # MCP subprocess 가 응답하지 않으면 TimeoutError 로 바꿔 재시도·폴백 경로를 탄다
MCP_RETRIES = 2     # with_retry 시도 횟수 = 최초 1회 + 재시도 1회


class MenuRepo(Protocol):
    async def list_menus(self) -> list[dict]: ...
    async def get_menu(self, name: str) -> dict | None: ...
    async def get_allergen_alias(self) -> dict[str, str | list[str]]: ...   # 값이 리스트면 여러 재료(예: 해산물)
    async def get_meal_history(self, user_id: str, days: int = 3) -> list[dict]: ...
    async def save_meal(self, user_id: str, menu_name: str) -> dict: ...
    def drain_fallbacks(self) -> list[str]: ...
    def drain_retries(self) -> int: ...


class LocalMenuRepo:
    def __init__(self, data_dir: Path = DATA_DIR, db_path: Path = meal_db.DB_PATH):
        self.data_dir = Path(data_dir)
        self.db_path = Path(db_path)
        self._conn = None

    def _db(self):
        if self._conn is None:
            self._conn = meal_db.connect(self.db_path)
        return self._conn

    def _menus(self) -> list[dict]:
        return json.loads((self.data_dir / "menus.json").read_text(encoding="utf-8"))

    async def list_menus(self) -> list[dict]:
        return self._menus()

    async def get_menu(self, name: str) -> dict | None:
        return next((m for m in self._menus() if m["name"] == name), None)

    async def get_allergen_alias(self) -> dict[str, str | list[str]]:
        return json.loads((self.data_dir / "allergen_alias.json").read_text(encoding="utf-8"))

    async def get_meal_history(self, user_id: str, days: int = 3) -> list[dict]:
        return meal_db.select_meals(self._db(), user_id, days)

    async def save_meal(self, user_id: str, menu_name: str) -> dict:
        if await self.get_menu(menu_name) is None:
            raise KeyError(menu_name)
        return meal_db.insert_meal(self._db(), user_id, menu_name)

    def drain_fallbacks(self) -> list[str]:
        return []

    def drain_retries(self) -> int:
        return 0

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class McpMenuRepo:
    def __init__(self, mcp: Any, fallback: LocalMenuRepo):
        self.mcp = mcp
        self.fallback = fallback
        self._fallbacks: list[str] = []
        self._retries = 0

    async def _call(self, fn, *args):
        async def once():
            return await asyncio.wait_for(fn(*args), timeout=MCP_TIMEOUT)
        result, retries = await with_retry(once, retries=MCP_RETRIES)
        self._retries += retries
        return result

    async def _tool(self, name: str, arguments: dict) -> dict:
        return await self._call(self.mcp.call_tool, name, arguments, MCP_SERVER)

    def _note(self, op: str, exc: Exception) -> None:
        if isinstance(exc, asyncio.TimeoutError):
            self._retries += MCP_RETRIES - 1   # 시간 초과로 재시도까지 다 쓴 경우
        self._fallbacks.append(f"{op}: MCP 실패 → 로컬 JSON")

    async def list_menus(self) -> list[dict]:
        try:
            return (await self._tool("list_menus", {}))["menus"]
        except Exception as e:
            self._note("list_menus", e)
            return await self.fallback.list_menus()

    async def get_menu(self, name: str) -> dict | None:
        try:
            return (await self._tool("get_menu", {"name": name}))["menu"]
        except Exception as e:
            self._note("get_menu", e)
            return await self.fallback.get_menu(name)

    async def get_allergen_alias(self) -> dict[str, str | list[str]]:
        try:
            text = await self._call(self.mcp.read_resource, "menu://allergens", MCP_SERVER)
            return json.loads(text)
        except Exception as e:
            self._note("get_allergen_alias", e)
            return await self.fallback.get_allergen_alias()

    async def get_meal_history(self, user_id: str, days: int = 3) -> list[dict]:
        try:
            return (await self._tool("get_meal_history",
                                     {"user_id": user_id, "days": days}))["items"]
        except Exception as e:
            self._note("get_meal_history", e)
            return await self.fallback.get_meal_history(user_id, days)

    async def save_meal(self, user_id: str, menu_name: str) -> dict:
        """없는 메뉴면 KeyError. MCP·로컬 모두 실패하면 예외를 그대로 올린다(서버가 503)."""
        try:
            result = await self._tool("save_meal", {"user_id": user_id, "menu_name": menu_name})
        except Exception as e:
            self._note("save_meal", e)
            return await self.fallback.save_meal(user_id, menu_name)
        if not result.get("ok"):
            raise KeyError(menu_name)
        return result["item"]

    def drain_fallbacks(self) -> list[str]:
        out, self._fallbacks = self._fallbacks, []
        return out

    def drain_retries(self) -> int:
        out, self._retries = self._retries, 0
        return out

    def close(self) -> None:
        self.fallback.close()
