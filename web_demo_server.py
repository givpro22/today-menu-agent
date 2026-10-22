"""web_demo_server.py — all_total_vue 에이전트와 브라우저 채팅창으로 대화해보는 데모.

web_chat.html이 바닐라 JS 대신 Vue 3(CDN 글로벌 빌드, 별도 빌드 단계 없음)로 되어
있다는 점만 all_total과 다르다. server.py(8100, 기존 통합 테스트 서버)는 전혀
건드리지 않는다. 이 스크립트는
- web_chat.html 한 장을 같은 출처(origin)로 띄우고
- /chat/stream, /chat/resume, /chat/pending, /meals, /graph 요청을 그대로 8100으로 중계(프록시)해서

브라우저의 CORS 제약 없이 실제 에이전트와 대화할 수 있게 해주는 아주 얇은 레이어다.
server.py는 평소처럼(fake/ollama/openai 아무 백엔드로나) 8100에 미리 띄워 놓아야 한다.

실행:
    python3 web_demo_server.py        # http://localhost:8600 에서 채팅 화면 오픈
"""
import json
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent
BACKEND_DOWN = {"detail": {"code": "BACKEND_UNAVAILABLE",
                           "message": "백엔드 서버(8100)에 연결할 수 없습니다. run.sh 로 다시 실행해 주세요."}}
BACKEND_SLOW = {"detail": {"code": "BACKEND_TIMEOUT",
                           "message": "백엔드 서버(8100)의 응답이 너무 오래 걸립니다. 잠시 뒤 다시 시도해 주세요."}}


def _proxy_error(exc: httpx.TransportError) -> tuple[int, dict]:
    """연결 실패 → 502 BACKEND_UNAVAILABLE, 연결은 됐는데 응답이 늦음(ReadTimeout) → 504 BACKEND_TIMEOUT."""
    if isinstance(exc, httpx.ReadTimeout):
        return 504, BACKEND_SLOW
    return 502, BACKEND_DOWN

app = FastAPI()


async def _forward(req: Request, path: str, timeout: float = 10.0) -> Response:
    """메서드·쿼리·본문을 그대로 8100 으로 넘기고 상태 코드·본문을 그대로 돌려준다."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(
                req.method, f"{BACKEND}{path}", params=req.query_params,
                content=await req.body(), headers={"content-type": "application/json"})
    except httpx.TransportError as e:
        status, body = _proxy_error(e)
        return JSONResponse(body, status_code=status)
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


@app.get("/")
async def index():
    return FileResponse(HERE / "web_chat.html")


@app.get("/health")
async def proxy_health(req: Request):
    return await _forward(req, "/health")


@app.post("/chat")
async def proxy_chat(req: Request):
    return await _forward(req, "/chat", timeout=120.0)


@app.post("/chat/resume")
async def proxy_resume(req: Request):
    return await _forward(req, "/chat/resume", timeout=120.0)


@app.get("/chat/pending")
async def proxy_pending(req: Request):
    return await _forward(req, "/chat/pending")


@app.api_route("/meals", methods=["GET", "POST"])
async def proxy_meals(req: Request):
    return await _forward(req, "/meals", timeout=30.0)


@app.get("/graph")
async def proxy_graph(req: Request):
    return await _forward(req, "/graph")


@app.get("/chat/stream")
async def proxy_stream(req: Request):
    params = dict(req.query_params)   # message, user_id

    async def relay():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", f"{BACKEND}/chat/stream", params=params) as upstream:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except httpx.TransportError as e:
            # EventSource 는 HTTP 오류 본문을 못 읽으므로 스트림 안에서 Error 이벤트로 알린다(계약 §2-4)
            status, body = _proxy_error(e)
            err = {"type": "Error", "status_code": status, **body}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode()

    return StreamingResponse(relay(), media_type="text/event-stream")


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="0.0.0.0", port=8600)
