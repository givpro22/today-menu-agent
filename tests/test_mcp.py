"""mcp_server.py Tool/Resource ↔ mcp_client ↔ McpMenuRepo (계약 §6). 실제 stdio subprocess 를 띄운다(네트워크 없음)."""
import asyncio
import json

import pytest
from conftest import MENUS, new_user, run

from agent import menu_repo as mr
from agent.mcp_client import MCPClient
from agent.menu_repo import LocalMenuRepo, McpMenuRepo

CONTRACT_TOOLS = {
    "list_menus": {"category"},
    "get_menu": {"name"},
    "get_meal_history": {"user_id", "days"},
    "save_meal": {"user_id", "menu_name"},
}


def test_mcp_tools_resources_and_shapes():
    async def go():
        mcp = MCPClient(flaky_first_tool_call=False)
        await mcp.start()
        try:
            s = mcp.sessions["menu"]
            tools = {t.name: set((getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}).get("properties", {}))
                     for t in (await s.list_tools()).tools}
            assert tools == CONTRACT_TOOLS
            res = [str(r.uri) for r in (await s.list_resources()).resources]
            assert res == ["menu://allergens"]
            assert (await s.list_prompts()).prompts == []            # 계약 D10

            lm = await mcp.call_tool("list_menus", {})
            assert set(lm) == {"menus", "count"} and lm["count"] == 36 and len(lm["menus"]) == 36
            assert {m["name"]: m for m in lm["menus"]} == MENUS
            assert (await mcp.call_tool("list_menus", {"category": "중식"}))["count"] == sum(m["category"] == "중식" for m in MENUS.values())
            assert await mcp.call_tool("get_menu", {"name": "된장찌개"}) == {"menu": MENUS["된장찌개"]}
            assert await mcp.call_tool("get_menu", {"name": "없는메뉴"}) == {"menu": None}
            u = new_user()
            saved = await mcp.call_tool("save_meal", {"user_id": u, "menu_name": "김밥"})
            assert saved["ok"] is True and set(saved["item"]) == {"id", "menu_name", "eaten_at"}
            bad = await mcp.call_tool("save_meal", {"user_id": u, "menu_name": "없는메뉴"})
            assert bad["ok"] is False and bad["error"]["code"] == "UNKNOWN_MENU"
            hist = await mcp.call_tool("get_meal_history", {"user_id": u, "days": 3})
            assert set(hist) == {"user_id", "days", "items"} and [i["menu_name"] for i in hist["items"]] == ["김밥"]
            alias = json.loads(await mcp.read_resource("menu://allergens"))
            assert alias["밀가루"] == "밀"

            # McpMenuRepo 는 MCP 와 LocalMenuRepo 와 같은 값을 돌려준다(같은 session.db)
            repo = McpMenuRepo(mcp, fallback=LocalMenuRepo())
            assert await repo.list_menus() == await LocalMenuRepo().list_menus()
            assert await repo.get_meal_history(u) == await LocalMenuRepo().get_meal_history(u)
            assert repo.drain_fallbacks() == [] and repo.drain_retries() == 0
            with pytest.raises(KeyError):
                await repo.save_meal(u, "없는메뉴")
        finally:
            await mcp.stop()
    run(go())


def test_flaky_first_call_retries_once():
    async def go():
        mcp = MCPClient(flaky_first_tool_call=True)
        await mcp.start()
        try:
            repo = McpMenuRepo(mcp, fallback=LocalMenuRepo())
            assert len(await repo.list_menus()) == 36
            assert repo.drain_retries() == 1 and repo.drain_fallbacks() == []
        finally:
            await mcp.stop()
    run(go())


class _NoSession:
    """MCP 기동 실패 상태(sessions 비어 있음)."""
    sessions = {}

    async def call_tool(self, name, arguments, server="menu"):
        return {}["menu"]

    async def read_resource(self, uri, server="menu"):
        return {}["menu"]


class _Hang:
    async def call_tool(self, name, arguments, server="menu"):
        await asyncio.sleep(10)

    async def read_resource(self, uri, server="menu"):
        await asyncio.sleep(10)


def test_mcp_down_falls_back_to_local_and_notes():
    async def go():
        repo = McpMenuRepo(_NoSession(), fallback=LocalMenuRepo())
        assert len(await repo.list_menus()) == 36
        assert (await repo.get_allergen_alias())["밀가루"] == "밀"
        item = await repo.save_meal(new_user(), "김밥")          # 로컬로 저장
        assert item["menu_name"] == "김밥"
        with pytest.raises(KeyError):
            await repo.save_meal(new_user(), "없는메뉴")
        notes = repo.drain_fallbacks()
        assert notes[0] == "list_menus: MCP 실패 → 로컬 JSON" and len(notes) == 4
        assert repo.drain_fallbacks() == []
    run(go())


def test_mcp_hang_times_out_then_falls_back(monkeypatch):
    """platform (4): MCP 가 응답하지 않으면 timeout → 재시도 → 로컬 폴백, 재시도 수 기록."""
    monkeypatch.setattr(mr, "MCP_TIMEOUT", 0.05)

    async def go():
        repo = McpMenuRepo(_Hang(), fallback=LocalMenuRepo())
        menus = await asyncio.wait_for(repo.list_menus(), timeout=3)
        assert len(menus) == 36
        assert repo.drain_retries() == 1 and repo.drain_fallbacks() == ["list_menus: MCP 실패 → 로컬 JSON"]
    run(go())


def test_fallback_notes_are_process_global():
    """platform 추가 항목 확인: 두 사용자 요청이 겹치면 한쪽 drain 이 다른 쪽 메모를 가져간다."""
    async def go():
        repo = McpMenuRepo(_NoSession(), fallback=LocalMenuRepo())
        await repo.list_menus()            # 사용자 A 요청 중 폴백
        b_view = repo.drain_fallbacks()    # 사용자 B 요청 시작/끝 drain
        a_view = repo.drain_fallbacks()    # A 응답 조립
        return a_view, b_view
    a_view, b_view = run(go())
    assert a_view == [] and b_view == ["list_menus: MCP 실패 → 로컬 JSON"]
