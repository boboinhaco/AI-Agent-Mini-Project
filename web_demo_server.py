"""web_demo_server.py — all_total_vue 에이전트와 브라우저 채팅창으로 대화해보는 데모.

web_chat.html이 바닐라 JS 대신 Vue 3(CDN 글로벌 빌드, 별도 빌드 단계 없음)로 되어
있다는 점만 all_total과 다르다. server.py(8100, 기존 통합 테스트 서버)는 전혀
건드리지 않는다. 이 스크립트는
- web_chat.html 한 장을 같은 출처(origin)로 띄우고
- /chat/stream 요청을 그대로 8100으로 중계(프록시)해서

브라우저의 CORS 제약 없이 실제 에이전트와 대화할 수 있게 해주는 아주 얇은 레이어다.
server.py는 평소처럼(fake/ollama/openai 아무 백엔드로나) 8100에 미리 띄워 놓아야 한다.

실행:
    python3 web_demo_server.py        # http://localhost:8600 에서 채팅 화면 오픈
"""
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse

BACKEND = "http://127.0.0.1:8100"
HERE = Path(__file__).parent

app = FastAPI()


@app.get("/")
async def index():
    return FileResponse(HERE / "web_chat.html")


@app.get("/health")
async def proxy_health():
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/health")
    return _passthrough(r)


@app.post("/chat")
async def proxy_chat(req: Request):
    body = await req.body()
    async with httpx.AsyncClient(timeout=600.0) as client:   # 실모델 장애 질문은 2~3분 걸릴 수 있다
        r = await client.post(f"{BACKEND}/chat", content=body,
                              headers={"content-type": "application/json"})
    return _passthrough(r)


def _relay(path: str, params: dict):
    async def relay():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{BACKEND}{path}", params=params) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
    return StreamingResponse(relay(), media_type="text/event-stream")


@app.get("/chat/stream")
async def proxy_stream(message: str, thread_id: str = "web-demo"):
    return _relay("/chat/stream", {"message": message, "thread_id": thread_id})


@app.get("/chat/approve/stream")
async def proxy_approve_stream(thread_id: str = "web-demo", approved: bool = True, comment: str = ""):
    """HITL — 승인/거절 후 이어지는 스트림도 그대로 중계."""
    return _relay("/chat/approve/stream",
                  {"thread_id": thread_id, "approved": str(approved).lower(), "comment": comment})


def _passthrough(r: httpx.Response):
    """백엔드 응답을 종류(JSON/HTML/텍스트) 그대로 넘긴다 — 500 본문이 JSON이 아니어도 깨지지 않게."""
    from fastapi.responses import Response
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/octet-stream"))


@app.get("/incidents")
async def proxy_incidents(limit: int = 50, format: str = "json"):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/incidents", params={"limit": limit, "format": format})
    return _passthrough(r)


@app.get("/incidents/{incident_id}")
async def proxy_incident(incident_id: int, format: str = "json"):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/incidents/{incident_id}", params={"format": format})
    return _passthrough(r)


@app.get("/session/{thread_id}")
async def proxy_session(thread_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BACKEND}/session/{thread_id}")
    return _passthrough(r)


if __name__ == "__main__":
    print("브라우저에서 http://localhost:8600 을 열어보세요 (server.py가 8100에 떠 있어야 합니다)")
    uvicorn.run(app, host="127.0.0.1", port=8600)   # 로컬 데모 — 외부 노출 안 함
