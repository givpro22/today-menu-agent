"""web_chat.html ↔ 실제 server 응답, web_demo_server.py 프록시 ↔ 화면 fetch 경로, .env.example ↔ getenv."""
import json
import re
import shutil
import socket
import subprocess

import pytest
from conftest import (CASE1, CASE3, GREETING, MANY_ALLERGIES, ROOT, new_user, parse_sse,
                      tmp_path_for)

NODE = shutil.which("node")
HTML = (ROOT / "web_chat.html").read_text(encoding="utf-8")


def _node(script, *args):
    out = subprocess.run([NODE, str(ROOT / "tests" / "js" / script), str(ROOT), *args],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_mock_matches_contract_examples():
    """회차 1 의 목업 ↔ 계약 예시 a~h deepEqual (13 항목)."""
    out = _node("mock_vs_contract.mjs")
    assert "FAIL 0" in out, out


@pytest.fixture
def real_frames(client):
    c = client()
    cases = []

    def stream(name, msg, uid, extra=None):
        frames = parse_sse(c.get("/chat/stream", params={"message": msg, "user_id": uid}).text)
        resps = [f["response"] for f in frames if isinstance(f, dict) and f["type"] == "FinalAnswer"]
        cases.append({"name": name, "frames": frames, "responses": resps + (extra or [])})

    stream("case1", CASE1, new_user())
    u = new_user()
    stream("case3", CASE3, u)
    approved = c.post("/chat/resume", json={"user_id": u, "approved": True}).json()
    cases.append({"name": "case3-approve", "frames": [], "responses": [approved]})
    u = new_user()
    stream("case3b", CASE3, u)
    rejected = c.post("/chat/resume", json={"user_id": u, "approved": False}).json()
    cases.append({"name": "case3-reject", "frames": [], "responses": [rejected]})
    stream("many-allergies", MANY_ALLERGIES, new_user())
    stream("greeting", GREETING, new_user())
    path = tmp_path_for(f"frames-{new_user()}.json")
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_real_events_render_without_errors(real_frames):
    res = json.loads(_node("render_real.mjs", str(real_frames)))
    assert res["errors"] == []
    known = {"RouteDecided", "ConstraintsExtracted", "CandidatesFiltered", "NeedsApproval", "RelaxDecision",
             "RelaxApplied", "Recommended", "Verified", "Reviewed", "Remembered", "NoResult", "Finalize"}
    for row in res["labels"]:
        if row["type"] in known:
            assert row["label"], row
    approval = sorted({r["label"] for r in res["labels"] if r["type"] == "approval"})
    assert approval == ["예산을 5,000원 → 8,500원으로 올려서 찾아볼까요? / 예산 5,000원 안에서는 조건에 맞는 메뉴가 없어요."]
    relax = [r["label"] for r in res["labels"] if r["case"] == "case3-approve" and r["type"] == "relaxLines"]
    assert relax == ["예산 5,000원 → 8,500원으로 바꿔서 찾았어요"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_relax_decision_null_is_not_shown_as_rejected(real_frames):
    """agent (a): 풀 조건이 없을 때 RelaxDecision.approved=null. 화면이 '거절됨' 이라고 쓰면 사실과 다르다."""
    res = json.loads(_node("render_real.mjs", str(real_frames)))
    rows = [r for r in res["labels"] if r["case"].startswith("many-allergies") and r["type"] == "RelaxDecision"]
    assert rows, "nothing_to_relax 경로에 RelaxDecision 이벤트가 있어야 함"
    for r in rows:
        assert "거절" not in r["label"], f"사용자가 거절하지 않았는데 화면 라벨: {r['label']}"


# ── 프록시 ──────────────────────────────────────────────────────────────────
def _frontend_calls():
    calls = set()
    for m in re.finditer(r"(apiFetch|postJson|openStream)\(\s*[`']([^`'?$]+)", HTML):
        fn, path = m.groups()
        calls.add(("POST" if fn == "postJson" else "GET", path))
    calls.add(("GET", "/chat/stream"))      # openStream(url) — url 변수
    return calls


def test_proxy_covers_every_frontend_call():
    import web_demo_server as w
    routes = {(meth, r.path) for r in w.app.routes for meth in getattr(r, "methods", set()) or set()}
    calls = _frontend_calls()
    assert {p for _, p in calls} == {"/health", "/chat/stream", "/chat/resume", "/chat/pending", "/meals"}
    missing = calls - routes
    assert not missing, missing
    assert ("GET", "/") in routes


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_proxy_backend_down(monkeypatch):
    """§2-1 v1.1: 8100 연결 실패 → JSON 502 BACKEND_UNAVAILABLE, SSE 는 200 스트림 안 Error 502 + [DONE]."""
    from fastapi.testclient import TestClient
    import web_demo_server as w
    monkeypatch.setattr(w, "BACKEND", f"http://127.0.0.1:{_free_port()}")
    c = TestClient(w.app)
    r = c.post("/chat/resume", json={"user_id": "u1", "approved": True})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "BACKEND_UNAVAILABLE"
    assert isinstance(r.json()["detail"]["message"], str)
    assert c.get("/meals", params={"user_id": "u1"}).status_code == 502
    frames = parse_sse(c.get("/chat/stream", params={"message": "점심", "user_id": "u1"}).text)
    assert frames[0]["type"] == "Error" and frames[0]["status_code"] == 502 and frames[-1] == "[DONE]"
    assert frames[0]["detail"]["code"] == "BACKEND_UNAVAILABLE"
    assert c.get("/").status_code == 200


def test_proxy_backend_slow_is_504(monkeypatch):
    """§2-1 v1.1: 연결은 됐는데 응답이 늦으면(ReadTimeout) 504 BACKEND_TIMEOUT. SSE 는 Error 504."""
    import httpx
    from fastapi.testclient import TestClient
    import web_demo_server as w

    async def slow_request(self, *a, **k):
        raise httpx.ReadTimeout("slow")

    def slow_stream(self, *a, **k):
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(httpx.AsyncClient, "request", slow_request)
    monkeypatch.setattr(httpx.AsyncClient, "stream", slow_stream)
    c = TestClient(w.app)
    r = c.post("/chat/resume", json={"user_id": "u1", "approved": True})
    assert r.status_code == 504 and r.json()["detail"]["code"] == "BACKEND_TIMEOUT"
    frames = parse_sse(c.get("/chat/stream", params={"message": "점심", "user_id": "u1"}).text)
    assert frames[0]["type"] == "Error" and frames[0]["status_code"] == 504
    assert frames[0]["detail"]["code"] == "BACKEND_TIMEOUT" and frames[-1] == "[DONE]"


# ── env ────────────────────────────────────────────────────────────────────
def test_env_example_lists_every_variable_code_reads():
    read = set()
    for f in [*(ROOT / "agent").rglob("*.py"), ROOT / "server.py", ROOT / "mcp_server.py",
              ROOT / "web_demo_server.py", ROOT / "run.sh"]:
        read |= set(re.findall(r"(?:getenv|environ\.get|environ\[)\(?\s*[\"']([A-Z][A-Z0-9_]+)[\"']", f.read_text(encoding="utf-8")))
        read |= set(re.findall(r'\$\{([A-Z][A-Z0-9_]+)[:}]', f.read_text(encoding="utf-8"))) if f.suffix == ".sh" else set()
    example = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
    assert read - example == set(), read - example
    assert "SUPERVISOR_VERSION" not in example and "SUPERVISOR_VERSION" not in read
