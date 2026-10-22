"""server.py ↔ 계약 §1~§3 ↔ 화면(04_frontend_fields.md). TestClient, fake LLM, MENU_REPO=local, 임시 DB."""
import threading

import pytest
from conftest import (CASE1, CASE1B, CASE3, CORIANDER, GREETING, MANY_ALLERGIES, MENUS, SEAFOOD,
                      SEAFOOD_SHORT, assert_safe, contract_example, contract_health, contract_version, forbidden_for,
                      frontend_reads, new_user, parse_sse, tmp_path_for)

VOLATILE = {"reason", "summary", "reply", "user_id", "prompt"}
VOLATILE_META = {"cost_usd", "elapsed_ms", "instance_id", "langsmith_run_id", "checkpointer",
                 "contract_version"}   # 버전은 assert_contract_shape 에서 계약 머리말과 따로 비교


_LAST_QUERY: dict[str, str] = {}   # user_id → 마지막 /chat 문장 (resume 응답의 정답을 찾으려고)


def post_chat(c, msg, uid):
    r = c.post("/chat", json={"user_id": uid, "message": msg})
    assert r.status_code == 200, r.text
    body = r.json()
    assert_safe(body, forbidden_for(msg))
    _LAST_QUERY[uid] = msg
    return body


def resume(c, uid, approved):
    r = c.post("/chat/resume", json={"user_id": uid, "approved": approved})
    if r.status_code == 200:
        assert_safe(r.json(), forbidden_for(_LAST_QUERY[uid]))
    return r


def strip(obj):
    """예시 문자열(reason/summary/reply)·실행마다 다른 숫자를 빼고 비교한다(계약 규칙: 키·enum·숫자 의미는 고정)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in VOLATILE or k in VOLATILE_META:
                continue
            out[k] = strip(v)
        return out
    if isinstance(obj, list):
        return [strip(x) for x in obj]
    return obj


# ── 계약 키 집합 ─────────────────────────────────────────────────────────────
A, B, Cx, D, E, F = (contract_example(x) for x in "abcdef")
TOP_KEYS = set(A)
META_KEYS = set(A["metadata"])
KEPT_KEYS = set(A["kept"][0])
RELAX_KEYS = set(Cx["relaxed"][0])
PROPOSAL_KEYS = set(B["proposal"])
FLAG_KEYS = set(F["review_flags"][0])
EVENT_KEYS = {}
for ex in (A, B, Cx, D, E):
    for ev in ex["events"]:
        EVENT_KEYS.setdefault(ev["type"], set(ev))


def assert_contract_shape(body):
    assert set(body) == TOP_KEYS
    assert set(body["metadata"]) == META_KEYS
    assert body["metadata"]["contract_version"] == contract_version()
    assert body["status"] in ("done", "needs_approval", "no_result", "chat")
    for k in body["kept"]:
        assert set(k) == KEPT_KEYS
    for r in body["relaxed"]:
        assert set(r) == RELAX_KEYS
    if body["proposal"] is not None:
        assert set(body["proposal"]) == PROPOSAL_KEYS
    for f in body["review_flags"]:
        assert set(f) == FLAG_KEYS
    for d in body["dropped"]:
        assert set(d) == {"name", "why"}
    assert set(body["excluded"]) == {"recent", "eaten"}
    for ev in body["events"]:
        assert "type" in ev and "node" in ev
        if ev["type"] in EVENT_KEYS:
            assert set(ev) == EVENT_KEYS[ev["type"]], (ev["type"], set(ev) ^ EVENT_KEYS[ev["type"]])
    if body["status"] == "needs_approval":
        assert body["trace"][-1] == "approve_relax" and body["proposal"] is not None
    if body["status"] == "no_result":
        assert body["no_result_reason"] in ("relax_rejected", "relax_exhausted", "nothing_to_relax", "verify_failed")
        assert body["reply"] == body["summary"]
    else:
        assert body["no_result_reason"] is None


def assert_frontend_fields(body):
    """화면이 읽는다고 적은 필드가 실제 응답에 전부 있다(값이 있을 때 하위 키까지)."""
    for path in frontend_reads():
        head, _, sub = path.partition(".")
        assert head in body, path
        val = body[head]
        if not sub:
            continue
        items = val if isinstance(val, list) else ([val] if isinstance(val, dict) else [])
        for it in items:
            assert sub in it, path


# ── 계약 예시와 값 비교 (a)~(e) ────────────────────────────────────────────────
def test_example_a_case1(client):
    c = client()
    body = post_chat(c, CASE1, new_user())
    assert_contract_shape(body)
    assert_frontend_fields(body)
    assert strip(body) == strip(A)


def test_example_b_c_case3_approve(client):
    c, u = client(), new_user()
    b = post_chat(c, CASE3, u)
    assert_contract_shape(b)
    assert_frontend_fields(b)
    assert strip(b) == strip(B)
    assert b["reply"] == B["reply"]                          # interrupt message 그대로
    p = c.get("/chat/pending", params={"user_id": u}).json()
    assert p == {"pending": True, "proposal": B["proposal"]}   # restorePending 이 읽는 shape
    r = resume(c, u, True)
    assert r.status_code == 200
    body = r.json()
    assert_contract_shape(body)
    assert_frontend_fields(body)
    assert strip(body) == strip(Cx)
    assert body["reply"].splitlines()[0] == "조건 완화: 예산 5,000원 → 8,500원"
    assert c.get("/chat/pending", params={"user_id": u}).json() == {"pending": False, "proposal": None}


def test_example_d_case3_reject(client):
    c, u = client(), new_user()
    post_chat(c, CASE3, u)
    body = resume(c, u, False).json()
    assert_contract_shape(body)
    assert strip(body) == strip(D)
    assert body["reply"] == D["reply"]


def test_example_e_chat(client):
    c = client()
    body = post_chat(c, GREETING, new_user())
    assert_contract_shape(body)
    assert_frontend_fields(body)
    assert strip(body) == strip(E)


def test_example_f_bad_reason(client):
    c = client(FAKE_LLM_MODE="bad_reason")
    body = post_chat(c, CASE1, new_user())
    k = next(k for k in body["kept"] if k["name"] == "북엇국")
    assert k == F["kept"][0]
    assert body["review_flags"] == F["review_flags"]


def test_excluded_definition_v1_1(client):
    """§1-1 excluded 정의(v1.1): recent = 오래된→최신(최대 6), eaten = 중복 제거·최신 먼저, 이벤트 숫자 = len(메뉴이름 ∩ 목록)."""
    c, u = client(), new_user()
    first = [k["name"] for k in post_chat(c, CASE1, u)["kept"]]
    second = [k["name"] for k in post_chat(c, CASE1B, u)["kept"]]
    for name in ("김밥", "된장찌개", "김밥"):
        c.post("/meals", json={"user_id": u, "menu_name": name})
    body = post_chat(c, CASE1B, u)
    assert body["excluded"]["recent"] == (first + second)[-6:]
    assert body["excluded"]["eaten"] == ["김밥", "된장찌개"]
    ev = next(e for e in body["events"] if e["type"] == "CandidatesFiltered")
    assert ev["excluded_recent"] == len(set(MENUS) & set(body["excluded"]["recent"]))
    assert ev["excluded_eaten"] == 2


def test_example_g_revisit_excluded(client):
    c, u = client(), new_user()
    first = [k["name"] for k in post_chat(c, CASE1, u)["kept"]]
    assert c.post("/meals", json={"user_id": u, "menu_name": "된장찌개"}).status_code == 200
    body = post_chat(c, CASE1B, u)
    assert body["excluded"] == contract_example("g")["excluded"]
    assert first == contract_example("g")["excluded"]["recent"]
    assert set(first).isdisjoint(k["name"] for k in body["kept"])


def test_example_h_errors(client):
    c = client()
    r = c.post("/chat", json={"user_id": new_user(), "message": "   "})
    assert r.status_code == 400 and r.json() == {"detail": {"code": "EMPTY_MESSAGE", "message": "메시지를 입력해 주세요."}}
    r = c.post("/chat/resume", json={"user_id": new_user(), "approved": True})
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "NO_PENDING_APPROVAL", "message": "승인 대기 중인 요청이 없습니다. 다시 질문해 주세요."}}


# ── 입력 검증 §2-1 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("payload,status,code", [
    ({"user_id": "bad id!", "message": "점심 추천"}, 400, "INVALID_USER_ID"),
    ({"user_id": "a" * 65, "message": "점심 추천"}, 400, "INVALID_USER_ID"),
    ({"message": "먹" * 501}, 400, "MESSAGE_TOO_LONG"),
    ({"user_id": "u1"}, 422, "INVALID_REQUEST"),
    ({"user_id": "u1", "message": 3}, 422, "INVALID_REQUEST"),
])
def test_chat_validation(client, payload, status, code):
    r = client().post("/chat", json=payload)
    assert r.status_code == status and r.json()["detail"]["code"] == code
    assert isinstance(r.json()["detail"]["message"], str)


def test_600_chars_rejected_500_ok_and_guest_default(client):
    c = client()
    assert c.post("/chat", json={"message": "먹" * 600}).json()["detail"]["code"] == "MESSAGE_TOO_LONG"
    body = post_chat(c, "점심 " + "먹" * 497, "guest")
    assert body["user_id"] == "guest"


@pytest.mark.parametrize("approved", ["yes", 1, None])
def test_resume_strict_bool(client, approved):
    r = client().post("/chat/resume", json={"user_id": "u1", "approved": approved})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_REQUEST"
    assert "errors" in r.json()["detail"]


def test_unrelated_question_goes_chat(client):
    body = post_chat(client(), "오늘 날씨 어때?", new_user())
    assert body["status"] == "chat" and body["constraints"] is None and body["kept"] == []


def test_unknown_allergy_term(client):
    body = post_chat(client(), CORIANDER, new_user())
    assert body["status"] == "done" and body["constraints"]["allergies"] == ["고수"]


def test_many_allergies_no_result(client):
    c, u = client(), new_user()
    body = post_chat(c, MANY_ALLERGIES, u)
    assert_contract_shape(body)
    assert body["status"] == "no_result" and body["no_result_reason"] == "nothing_to_relax"
    assert c.get("/chat/pending", params={"user_id": u}).json()["pending"] is False


# ── 해산물 회귀 (F2-1) ─────────────────────────────────────────────────────
@pytest.mark.parametrize("msg", [SEAFOOD, SEAFOOD_SHORT])
@pytest.mark.parametrize("mode", ["normal", "bad_reason", "off_menu"])
def test_seafood_regression_json_and_sse(client, msg, mode):
    """해산물 알레르기 문장: JSON·SSE 두 경로, fake 3모드 모두에서 정답표(새우·오징어·조개·생선) 재료 0건.
    assert_safe 가 추출 누락(constraints.allergies 가 정답을 덮지 않음)도 같이 잡는다."""
    c = client(FAKE_LLM_MODE=mode)
    body = post_chat(c, msg, new_user())
    assert body["status"] in ("done", "needs_approval", "no_result")
    frames = sse(c, msg, new_user())
    assert_safe(frames[-2]["response"], forbidden_for(msg))


def test_seafood_meals_then_revisit(client):
    """먹은 메뉴·최근 추천 제외가 겹쳐 후보가 줄어도 해산물 재료가 끼어들지 않는다."""
    c, u = client(), new_user()
    c.post("/meals", json={"user_id": u, "menu_name": "된장찌개"})
    for _ in range(3):
        body = post_chat(c, SEAFOOD, u)
        if body["status"] == "needs_approval":
            resume(c, u, True)


# ── meals ──────────────────────────────────────────────────────────────────
def test_meals_roundtrip_and_errors(client):
    c, u = client(), new_user()
    r = c.post("/meals", json={"user_id": u, "menu_name": "된장찌개"})
    assert r.status_code == 200
    item = r.json()["item"]
    assert r.json()["ok"] is True and set(item) == {"id", "menu_name", "eaten_at"}
    c.post("/meals", json={"user_id": u, "menu_name": "김밥"})
    g = c.get("/meals", params={"user_id": u, "days": 7}).json()
    assert set(g) == {"user_id", "days", "items"} and g["days"] == 7
    assert [i["menu_name"] for i in g["items"]] == ["김밥", "된장찌개"]       # 최신순
    assert c.get("/meals", params={"user_id": u}).json()["days"] == 7
    r = c.post("/meals", json={"user_id": u, "menu_name": "없는메뉴"})
    assert r.status_code == 404 and r.json()["detail"] == {"code": "UNKNOWN_MENU", "message": "메뉴 목록에 없는 이름입니다: 없는메뉴"}
    for days in (0, 31):
        assert c.get("/meals", params={"user_id": u, "days": days}).status_code == 422


def test_meal_store_unavailable_503(client, monkeypatch):
    c = client()

    async def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(c.app.state.deps.menu_repo, "save_meal", boom)
    r = c.post("/meals", json={"user_id": "u1", "menu_name": "된장찌개"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "MEAL_STORE_UNAVAILABLE"


# ── SSE §2-4 ───────────────────────────────────────────────────────────────
def sse(c, msg, uid):
    r = c.get("/chat/stream", params={"message": msg, "user_id": uid})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


def test_sse_case1_matches_json(client):
    c = client()
    frames = sse(c, CASE1, new_user())
    assert frames[-1] == "[DONE]"
    fa = frames[-2]
    assert fa["type"] == "FinalAnswer" and fa["reply"] == fa["response"]["reply"] and fa["prompt"] == fa["response"]["prompt"]
    body = fa["response"]
    assert_contract_shape(body)
    assert_safe(body, forbidden_for(CASE1))
    evs = [f for f in frames[:-2] if f["type"] != "AnswerToken"]
    assert evs == body["events"]
    assert strip(body) == strip(A)


def test_sse_case3_then_resume(client):
    c, u = client(), new_user()
    frames = sse(c, CASE3, u)
    _LAST_QUERY[u] = CASE3
    types = [f["type"] for f in frames[:-1]]
    assert types[-2:] == ["NeedsApproval", "FinalAnswer"]
    body = frames[-2]["response"]
    assert body["status"] == "needs_approval" and body["events"] == frames[:-2]
    assert frames[-3]["proposal"] == body["proposal"]
    r = resume(c, u, True)
    assert r.status_code == 200 and r.json()["status"] == "done"


def test_sse_chat_tokens(client):
    frames = sse(client(), GREETING, new_user())
    tokens = "".join(f["delta"] for f in frames if isinstance(f, dict) and f["type"] == "AnswerToken")
    assert tokens == frames[-2]["response"]["reply"]


@pytest.mark.parametrize("msg,uid,status,code", [
    ("   ", "u1", 400, "EMPTY_MESSAGE"),
    ("먹" * 501, "u1", 400, "MESSAGE_TOO_LONG"),
    ("점심", "bad id!", 400, "INVALID_USER_ID"),
])
def test_sse_errors_inside_stream(client, msg, uid, status, code):
    frames = sse(client(), msg, uid)
    assert frames[-1] == "[DONE]" and len(frames) == 2
    assert frames[0]["type"] == "Error" and frames[0]["status_code"] == status and frames[0]["detail"]["code"] == code


# ── D8 / 동시성 / 재시작 ─────────────────────────────────────────────────────
def test_d8_new_question_cancels_pending(client):
    c, u = client(), new_user()
    post_chat(c, CASE3, u)
    body = post_chat(c, CASE1, u)
    assert body["metadata"]["cancelled_pending"] is True and body["status"] == "done"
    assert c.get("/chat/pending", params={"user_id": u}).json()["pending"] is False
    assert resume(c, u, True).status_code == 409


def test_double_resume_one_wins(client):
    """platform (3): 같은 사용자 승인 두 번 동시 → 하나 200, 하나 409."""
    c, u = client(), new_user()
    post_chat(c, CASE3, u)
    results = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        results.append(c.post("/chat/resume", json={"user_id": u, "approved": True}).status_code)
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 409]


def test_restart_keeps_memory_and_pending(client):
    ckpt = str(tmp_path_for(f"ckpt-api-{new_user()}.db"))
    u1, u2 = new_user(), new_user()
    c = client(CHECKPOINTER="sqlite", MENU_CHECKPOINT_DB=ckpt)
    first = [k["name"] for k in post_chat(c, CASE1, u1)["kept"]]
    assert post_chat(c, CASE3, u2)["status"] == "needs_approval"
    c.__exit__(None, None, None)
    c2 = client(CHECKPOINTER="sqlite", MENU_CHECKPOINT_DB=ckpt)       # 새 lifespan = 재시작
    assert c2.app.state.instance_id != 0
    body = post_chat(c2, CASE1B, u1)
    assert body["excluded"]["recent"] == first
    assert set(first).isdisjoint(k["name"] for k in body["kept"])
    assert c2.get("/chat/pending", params={"user_id": u2}).json()["pending"] is True
    r = resume(c2, u2, True)
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["metadata"]["checkpointer"] == "sqlite"


def test_history_append_on_resume(client):
    c, u = client(), new_user()
    post_chat(c, CASE3, u)
    resume(c, u, False)
    hist = c.app.state.deps.session_store.load(u)
    assert [h["content"] for h in hist][-2] == "(조건 완화 거절)"
    assert hist[-1]["content"] == D["reply"]


def test_name_memory_through_api(client):
    c, u = client(), new_user()
    post_chat(c, "제 이름은 민수입니다", u)
    assert "민수" in post_chat(c, "제 이름이 뭐였죠?", u)["reply"]


# ── 오류 분류 ───────────────────────────────────────────────────────────────
def test_llm_error_502_from_llm_module(client, monkeypatch):
    import agent.fake_llm as fl
    c = client()

    def boom(q):
        raise RuntimeError("simulated provider failure")
    monkeypatch.setattr(fl, "rule_route", boom)
    r = c.post("/chat", json={"user_id": "u1", "message": "점심 추천"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "LLM_ERROR"
    frames = sse(c, "점심 추천", "u1")
    assert frames[0]["type"] == "Error" and frames[0]["status_code"] == 502


def test_openai_without_key_is_llm_error_no_network(client, monkeypatch):
    """LLM 키 없음 → 502 LLM_ERROR. httpx.post 는 막아서 실제 호출이 나가면 실패한다."""
    import agent.openai_llm as ol
    c = client()

    def no_network(*a, **k):
        raise AssertionError("실제 OpenAI 호출 시도")
    monkeypatch.setattr(ol.httpx, "post", no_network)
    llm = ol.OpenAIFastLLM("gpt-4o-mini", usd_per_1k_tokens=0.00015)
    llm.api_key = ""
    monkeypatch.setattr(c.app.state.deps, "fast_llm", llm)
    r = c.post("/chat", json={"user_id": "u1", "message": "점심 추천"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "LLM_ERROR"
    assert "OPENAI_API_KEY" in r.json()["detail"]["message"]


def test_non_llm_error_is_internal_500(client, monkeypatch):
    c = client()

    async def boom():
        raise RuntimeError("repo broken")
    monkeypatch.setattr(c.app.state.deps.menu_repo, "list_menus", boom)
    r = c.post("/chat", json={"user_id": "u1", "message": "점심 추천"})
    assert r.status_code == 500 and r.json()["detail"] == {"code": "INTERNAL_ERROR", "message": "서버 오류가 발생했습니다."}


def test_bug_inside_llm_module_is_misreported_as_llm_error(client, monkeypatch):
    """platform (2) 확인: provider 가 아닌 코드 버그라도 *_llm.py 프레임이면 LLM_ERROR 로 분류된다."""
    import agent.fake_llm as fl
    c = client()
    monkeypatch.setattr(fl, "rule_extract", lambda q: {}["no_such_key"])
    r = c.post("/chat", json={"user_id": "u1", "message": "점심 추천"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "LLM_ERROR"


# ── health / graph ─────────────────────────────────────────────────────────
def test_health_and_graph(client):
    c = client()
    h = c.get("/health").json()
    assert set(h) == set(contract_health())
    assert h["menu_count"] == len(MENUS) == 36 and h["contract_version"] == contract_version() and h["creation_count"] >= 1
    assert c.get("/version").json() == {"contract_version": contract_version(), "service": "today-menu-agent"}
    mermaid = c.get("/graph").json()["mermaid"]
    for n in ("route", "chat_answer", "extract", "filter", "approve_relax", "relax", "recommend",
              "verify", "review", "remember", "no_result", "finalize"):
        assert n in mermaid
