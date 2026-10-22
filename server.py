"""오늘뭐먹지 — FastAPI 백엔드 (sample all_total Wave 1·2·3·12 축 + 전체 조립).

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·체크포인터·그래프)을 프로세스당 정확히 1회 생성,
            app.state로 모든 요청이 공유 (creation_count/instance_id로 증명)
- Wave 2  : mcp_server.py를 subprocess(stdio)로 기동해 연결 유지. 그래프는 menu_repo 로만 접근
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력·최근 추천을 SQLite(session.db)에 저장 — 재시작해도 유지
- HITL    : 조건 완화 전 interrupt → POST /chat/resume 로 승인/거절. 대기 상태는 checkpoints.db
- LangSmith: 요청당 chat_request / chat_resume_request 루트 run 아래로 노드·LLM·MCP 호출이 기록됨

응답 shape 은 _workspace/01_architect_contracts.md (v1.0) 가 원천이다.
"""
import asyncio
import json
import logging
import os
import re
import time
import traceback
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT / MENU_DB_PATH — import보다 먼저

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.types import Command
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel, StrictBool

from agent.deps import SupervisorDeps
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.menu_repo import LocalMenuRepo, McpMenuRepo
from agent.rules import RECENT_LIMIT
from agent.session_store import SqliteSessionStore
from agent.state import initial_state

ROOT = Path(__file__).parent
CONTRACT_VERSION = "1.2"
MAX_MESSAGE_CHARS = 500
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)

log = logging.getLogger("today-menu")


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    user_id: str = "guest"
    message: str


class ResumeRequest(BaseModel):
    user_id: str = "guest"
    approved: StrictBool          # "yes", 1 같은 값은 422


class MealRequest(BaseModel):
    user_id: str = "guest"
    menu_name: str


class ApiError(Exception):
    """계약 §2-1 의 오류. 본문은 {"detail": {"code", "message"}}."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message

    def body(self) -> dict:
        return {"detail": {"code": self.code, "message": self.message}}


def _check_user_id(user_id: str) -> str:
    if not USER_ID_RE.match(user_id or ""):
        raise ApiError(400, "INVALID_USER_ID", "사용자 ID 형식이 올바르지 않습니다.")
    return user_id


def _check_message(message: str) -> str:
    message = (message or "").strip()
    if not message:
        raise ApiError(400, "EMPTY_MESSAGE", "메시지를 입력해 주세요.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise ApiError(400, "MESSAGE_TOO_LONG", f"메시지는 {MAX_MESSAGE_CHARS}자 이내로 입력해 주세요.")
    return message


def _is_llm_error(exc: BaseException) -> bool:
    """LLM provider 모듈(agent/*_llm.py) 안에서 난 예외이거나 httpx 오류면 LLM 실패로 본다."""
    if isinstance(exc, httpx.HTTPError):
        return True
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_filename.endswith("_llm.py"):
            return True
        tb = tb.tb_next
    return False


def _to_api_error(exc: BaseException) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    log.error("request failed: %s", "".join(traceback.format_exception(exc)))
    if _is_llm_error(exc):
        summary = f"{type(exc).__name__}: {str(exc)[:200]}" if str(exc) else type(exc).__name__
        return ApiError(502, "LLM_ERROR", f"LLM 호출에 실패했습니다: {summary}")
    return ApiError(500, "INTERNAL_ERROR", "서버 오류가 발생했습니다.")


def _build_llms():
    """LLM_BACKEND=fake(기본, 결정론) | ollama(로컬 실모델) | openai(실제 OpenAI API)."""
    backend = os.environ.get("LLM_BACKEND", "fake")
    if backend == "ollama":
        from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
        fast = OllamaFastLLM(os.environ.get("OLLAMA_FAST_MODEL", "qwen3:0.6b"),
                             usd_per_1k_tokens=0.00015)
        quality = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    if backend == "openai":
        from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
        fast = OpenAIFastLLM(os.environ.get("OPENAI_FAST_MODEL", "gpt-4o-mini"),
                             usd_per_1k_tokens=0.00015)
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o-mini"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    from agent.fake_llm import FastLLM, QualityLLM
    mode = os.environ.get("FAKE_LLM_MODE", "normal")
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015, mode=mode)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005, mode=mode)
    return "fake", fast, quality


async def _open_checkpointer(stack: AsyncExitStack):
    """CHECKPOINTER=sqlite(기본, checkpoints.db) | memory. 승인 대기 상태만 맡는다(계약 D3·D4)."""
    kind = os.environ.get("CHECKPOINTER", "sqlite")
    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        return "memory", MemorySaver()
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    path = os.environ.get("MENU_CHECKPOINT_DB") or str(ROOT / "checkpoints.db")
    saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(path))
    return "sqlite", saver


async def _open_menu_repo(stack: AsyncExitStack):
    """MENU_REPO=mcp(기본) | local. MCP 기동이 실패해도 서버는 뜨고 호출마다 로컬 폴백한다."""
    local = LocalMenuRepo()
    stack.callback(local.close)
    if os.environ.get("MENU_REPO", "mcp") == "local":
        return "local", local
    mcp = MCPClient()
    stack.push_async_callback(mcp.stop)
    try:
        await mcp.start()   # Wave 2 — MCP subprocess는 서버 수명 동안 딱 1개
    except Exception as e:
        log.warning("MCP 서버 기동 실패, 로컬 JSON 폴백으로 동작: %r", e)
    return "mcp", McpMenuRepo(mcp, fallback=local)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    async with AsyncExitStack() as stack:
        repo_kind, menu_repo = await _open_menu_repo(stack)
        checkpointer_kind, checkpointer = await _open_checkpointer(stack)
        backend, fast_llm, quality_llm = _build_llms()
        session_store = SqliteSessionStore()
        stack.callback(session_store.close)
        deps = SupervisorDeps(
            fast_llm=fast_llm,
            quality_llm=quality_llm,
            menu_repo=menu_repo,
            session_store=session_store,
            review_mode=os.environ.get("REVIEW_MODE", "rules"),
        )
        app.state.deps = deps
        app.state.graph = build_graph(deps, checkpointer=checkpointer)
        app.state.llm_backend = backend
        app.state.checkpointer = checkpointer_kind
        app.state.menu_repo_kind = repo_kind
        app.state.menu_count = len(await LocalMenuRepo().list_menus())
        app.state.instance_id = id(deps)
        app.state.user_locks = {}
        yield
    try:   # 종료 전 LangSmith 배치 전송 플러시 — kill 시 run이 pending으로 남는 것 방지
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(exc.body(), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
              for e in exc.errors()]
    return JSONResponse(
        {"detail": {"code": "INVALID_REQUEST", "message": "요청 형식이 올바르지 않습니다.",
                    "errors": jsonable_encoder(errors)}},
        status_code=422)


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    err = _to_api_error(exc)
    return JSONResponse(err.body(), status_code=err.status_code)


def _user_lock(user_id: str) -> asyncio.Lock:
    """같은 thread 에 두 요청이 겹치면(승인 버튼 두 번 등) 체크포인트가 꼬이므로 사용자별로 직렬화."""
    return app.state.user_locks.setdefault(user_id, asyncio.Lock())


def _config(user_id: str) -> dict:
    deps = app.state.deps
    return {"configurable": {"thread_id": user_id},
            "run_name": "menu_graph",
            "tags": ["today-menu", f"backend:{app.state.llm_backend}", f"review:{deps.review_mode}"],
            "metadata": {"user_id": user_id}}


async def _pending_payload(config: dict) -> dict | None:
    snapshot = await app.state.graph.aget_state(config)
    interrupts = getattr(snapshot, "interrupts", ()) or ()
    return interrupts[0].value if interrupts else None


def _drain_repo() -> tuple[list[str], int]:
    repo = app.state.deps.menu_repo
    return repo.drain_fallbacks(), repo.drain_retries()


def _build_response(state: dict, payload: dict | None, user_id: str, *, started: float,
                    cancelled_pending: bool, run_id: str | None) -> dict:
    """그래프 최종 State(+ interrupt payload) → ChatResponse (계약 §1-1)."""
    deps = app.state.deps
    route = state.get("route") or "menu"
    kept = state.get("kept") or []
    if route == "chat":
        status = "chat"
    elif payload is not None:
        status = "needs_approval"
    elif kept:
        status = "done"
    else:
        status = "no_result"

    trace = list(state.get("trace") or [])
    events = list(state.get("events") or [])
    reply = state.get("final") or ""
    proposal = None
    if status == "needs_approval":
        proposal = payload.get("proposal")
        trace.append("approve_relax")
        events.append({"type": "NeedsApproval", "node": "approve_relax", "proposal": proposal})
        reply = payload.get("message") or ""

    fallbacks, retries = _drain_repo()
    return {
        "status": status,
        "route": route,
        "user_id": user_id,
        "reply": reply,
        "kept": kept if status == "done" else [],
        "relaxed": state.get("relaxed") or [],
        "proposal": proposal,
        "summary": (state.get("summary") or "") if status in ("done", "no_result") else "",
        "no_result_reason": state.get("no_result_reason") if status == "no_result" else None,
        "review_flags": state.get("review_flags") or [],
        "dropped": state.get("dropped") or [],
        "constraints": (state.get("constraints") or None) if route != "chat" else None,
        "excluded": {"recent": list(state.get("recent") or []) if route != "chat" else [],
                     "eaten": list(state.get("eaten") or []) if route != "chat" else []},
        "trace": trace,
        "events": events,
        "prompt": state.get("last_prompt"),
        "metadata": {
            "contract_version": CONTRACT_VERSION,
            "route": route,
            "llm_backend": app.state.llm_backend,
            "fast_llm": deps.fast_llm.name,
            "quality_llm": deps.quality_llm.name,
            "review_mode": deps.review_mode,
            "llm_calls": int(state.get("llm_calls") or 0),
            "cost_usd": round(float(state.get("cost_usd") or 0.0), 6),
            "attempts": int(state.get("attempts") or 0),
            "relax_count": int((payload or {}).get("relax_count", state.get("relax_count") or 0)),
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "fallback": list(state.get("fallback") or []) + fallbacks,
            "mcp_retries": retries,
            "cancelled_pending": cancelled_pending,
            "checkpointer": app.state.checkpointer,
            "instance_id": app.state.instance_id,
            "langsmith_run_id": run_id,
        },
    }


def _run_id() -> str | None:
    rt = get_current_run_tree()
    return str(rt.id) if rt else None


async def _start_chat(message: str, user_id: str, config: dict) -> tuple[dict, bool]:
    """/chat·/chat/stream 공통 준비: 대기 승인 확인(D8), 최근 추천·대화 기록 읽기."""
    deps = app.state.deps
    cancelled = await _pending_payload(config) is not None
    recent = deps.session_store.recent_recommendations(user_id, limit=RECENT_LIMIT)
    history = deps.session_store.load(user_id)   # Wave 12
    _drain_repo()   # 이전 요청(/meals 등)이 남긴 폴백 메모는 이번 응답과 무관
    return initial_state(message, user_id, history, recent), cancelled


@traceable(name="chat_request")
async def _handle_chat(message: str, user_id: str) -> dict:
    started = time.perf_counter()
    config = _config(user_id)
    inputs, cancelled = await _start_chat(message, user_id, config)
    result = await app.state.graph.ainvoke(inputs, config)
    interrupts = result.get("__interrupt__") or ()
    resp = _build_response(result, interrupts[0].value if interrupts else None, user_id,
                           started=started, cancelled_pending=cancelled, run_id=_run_id())
    store = app.state.deps.session_store
    store.append(user_id, "user", message)
    store.append(user_id, "assistant", resp["reply"])
    return resp


@traceable(name="chat_resume_request")
async def _handle_resume(user_id: str, approved: bool) -> dict:
    started = time.perf_counter()
    config = _config(user_id)
    # 대기 없는 thread 에 Command(resume) 를 보내면 그래프가 빈 입력으로 START 부터 돈다(실측) → 409
    if await _pending_payload(config) is None:
        raise ApiError(409, "NO_PENDING_APPROVAL", "승인 대기 중인 요청이 없습니다. 다시 질문해 주세요.")
    _drain_repo()
    result = await app.state.graph.ainvoke(Command(resume={"approved": approved}), config)
    interrupts = result.get("__interrupt__") or ()
    resp = _build_response(result, interrupts[0].value if interrupts else None, user_id,
                           started=started, cancelled_pending=False, run_id=_run_id())
    store = app.state.deps.session_store
    store.append(user_id, "user", "(조건 완화 승인)" if approved else "(조건 완화 거절)")
    store.append(user_id, "assistant", resp["reply"])
    return resp


@app.get("/health")
async def health():
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "llm_backend": app.state.llm_backend,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name,
            "review_mode": app.state.deps.review_mode,
            "checkpointer": app.state.checkpointer,
            "menu_repo": app.state.menu_repo_kind,
            "menu_count": app.state.menu_count,
            "contract_version": CONTRACT_VERSION}


@app.get("/version")
async def version():
    return {"contract_version": CONTRACT_VERSION, "service": "today-menu-agent"}


@app.post("/chat")
async def chat(req: ChatRequest):
    user_id = _check_user_id(req.user_id)
    message = _check_message(req.message)
    async with _user_lock(user_id):
        return await _handle_chat(message, user_id)


@app.post("/chat/resume")
async def chat_resume(req: ResumeRequest):
    user_id = _check_user_id(req.user_id)
    async with _user_lock(user_id):
        return await _handle_resume(user_id, req.approved)


@app.get("/chat/pending")
async def chat_pending(user_id: str = "guest"):
    user_id = _check_user_id(user_id)
    payload = await _pending_payload(_config(user_id))
    return {"pending": payload is not None, "proposal": (payload or {}).get("proposal")}


def _sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.get("/chat/stream")
async def chat_stream(message: str = "", user_id: str = "guest"):
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍 (계약 §2-4).

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다. 마지막 FinalAnswer 에는 POST /chat 과 같은 ChatResponse 가 실린다.
    EventSource 는 HTTP 오류 본문을 못 읽으므로 검증·실행 오류도 200 스트림 안의 Error 이벤트로 보낸다.
    """
    async def gen():
        try:
            uid = _check_user_id(user_id)
            msg = _check_message(message)
            async with _user_lock(uid):
                started = time.perf_counter()
                config = _config(uid)
                inputs, cancelled = await _start_chat(msg, uid, config)
                seen = 0
                # stream_mode를 리스트로 주면 (mode, payload) 튜플이 나온다 — "updates"는
                # 노드가 끝날 때마다의 상태 델타, "custom"은 chat_answer_node가
                # get_stream_writer()로 실행 중에 즉시 흘려보내는 토큰(AnswerToken) 이벤트다.
                async for mode, chunk in app.state.graph.astream(
                        inputs, config, stream_mode=["updates", "custom"]):
                    if mode == "custom":
                        yield _sse(chunk)
                        continue
                    for node_name, delta in chunk.items():
                        if node_name == "__interrupt__":
                            proposal = (delta[0].value or {}).get("proposal") if delta else None
                            yield _sse({"type": "NeedsApproval", "node": "approve_relax",
                                        "proposal": proposal})
                            continue
                        new_events = (delta or {}).get("events") or []
                        for ev in new_events[seen:]:
                            yield _sse({**ev, "node": ev.get("node", node_name)})
                        if new_events:
                            seen = len(new_events)
                snapshot = await app.state.graph.aget_state(config)
                interrupts = getattr(snapshot, "interrupts", ()) or ()
                resp = _build_response(snapshot.values, interrupts[0].value if interrupts else None,
                                       uid, started=started, cancelled_pending=cancelled, run_id=None)
                store = app.state.deps.session_store
                store.append(uid, "user", msg)
                store.append(uid, "assistant", resp["reply"])
            yield _sse({"type": "FinalAnswer", "reply": resp["reply"], "prompt": resp["prompt"],
                        "response": resp})
        except Exception as e:
            err = _to_api_error(e)
            yield _sse({"type": "Error", "status_code": err.status_code, **err.body()})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/meals")
async def save_meal(req: MealRequest):
    user_id = _check_user_id(req.user_id)
    name = req.menu_name.strip()
    try:
        item = await app.state.deps.menu_repo.save_meal(user_id, name)
    except KeyError:
        raise ApiError(404, "UNKNOWN_MENU", f"메뉴 목록에 없는 이름입니다: {name}")
    except Exception as e:
        log.error("save_meal failed: %r", e)
        raise ApiError(503, "MEAL_STORE_UNAVAILABLE", "식사 기록 저장소에 연결할 수 없습니다.")
    return {"ok": True, "item": item}


@app.get("/meals")
async def list_meals(user_id: str = "guest", days: int = Query(7, ge=1, le=30)):
    user_id = _check_user_id(user_id)
    try:
        items = await app.state.deps.menu_repo.get_meal_history(user_id, days)
    except Exception as e:
        log.error("get_meal_history failed: %r", e)
        raise ApiError(503, "MEAL_STORE_UNAVAILABLE", "식사 기록 저장소에 연결할 수 없습니다.")
    return {"user_id": user_id, "days": days, "items": items}


@app.get("/graph")
async def graph_mermaid():
    return {"mermaid": app.state.graph.get_graph().draw_mermaid()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
