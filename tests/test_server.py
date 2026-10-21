"""서버 계층 — FastAPI를 ASGI로 직접 호출 (fake LLM + FakeMCP, MCP subprocess 없음)."""
import json

import httpx
import pytest

import server as srv
from agent.graph import build_graph
from conftest import FakeMCP


@pytest.fixture
async def client(deps):
    """lifespan을 거치지 않고 app.state를 직접 구성 — MCP subprocess 대신 FakeMCP."""
    deps.hitl_enabled = True
    deps.mcp = FakeMCP()
    srv.app.state.deps = deps
    srv.app.state.llm_backend = "fake"
    srv.app.state.graph = build_graph(deps)
    srv.app.state.instance_id = 1
    srv.app.state.pending = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://t") as c:
        yield c


async def sse_events(client, path, **params):
    async with client.stream("GET", path, params=params) as r:
        body = (await r.aread()).decode()
    out = []
    for chunk in body.split("\n\n"):
        if chunk.startswith("data: ") and chunk != "data: [DONE]":
            out.append(json.loads(chunk[6:]))
    return out, body.strip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_chat_then_single_approval(client):
    r = (await client.post("/chat", json={"message": "cache-service 메모리 사용률이 계속 올라가고 있어요",
                                          "thread_id": "s1"})).json()
    assert r["status"] == "approval_required" and "서비스/프로세스 재시작" in r["approval"]["actions"]
    r2 = (await client.post("/chat/approve", json={"thread_id": "s1", "approved": True})).json()
    assert r2["status"] == "done"                                   # 승인은 한 번이면 끝 (리뷰 A5)
    assert [e["type"] for e in r2["events"]].count("ApprovalGranted") == 1
    assert r2["reply"].startswith("📌 cache-service")                 # 코드 머리말 (리뷰 B2)
    assert r2["incident_id"] and srv.app.state.pending == {}
    # 세션: user 1 + assistant 1 (승인 대기 때 저장한 user가 중복되지 않음, 리뷰 A2)
    hist = srv.app.state.deps.session_store.load("s1")
    assert [m["role"] for m in hist] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_reject_then_escalate_without_reasking(client):
    r = (await client.post("/chat", json={"message": "cache-service 메모리 사용률이 계속 올라가고 있어요",
                                          "thread_id": "s2"})).json()
    assert r["status"] == "approval_required"
    r2 = (await client.post("/chat/approve", json={"thread_id": "s2", "approved": False,
                                                    "comment": "재시작은 안 돼요"})).json()
    # fake 작성기는 거절 피드백을 반영하지 못해 같은 조치를 또 넣는다 → 다시 묻지 않고 경고로 종료 (리뷰 B1)
    assert r2["status"] == "done"
    types = [e["type"] for e in r2["events"]]
    assert "ApprovalRejected" in types and "ApprovalEscalated" in types
    assert "실행하지 마세요" in r2["reply"]


@pytest.mark.asyncio
async def test_new_message_discards_pending(client):
    r = (await client.post("/chat", json={"message": "cache-service 메모리 사용률이 계속 올라가고 있어요",
                                          "thread_id": "s3"})).json()
    assert r["status"] == "approval_required"
    r2 = (await client.post("/chat", json={"message": "안녕", "thread_id": "s3"})).json()
    assert r2["events"][0]["type"] == "PendingDiscarded"              # 리뷰 A1
    assert (await client.post("/chat/approve", json={"thread_id": "s3", "approved": True})).status_code == 404
    roles = [m["role"] for m in srv.app.state.deps.session_store.load("s3")]
    assert roles == ["user", "assistant", "user", "assistant"]        # 대기 턴 종료 기록 + 새 턴


@pytest.mark.asyncio
async def test_stream_has_subagent_events_and_done(client):
    # 위험 조치가 없는 질문(네트워크 타임아웃 매뉴얼은 점검·우회 위주) → 승인 없이 FinalAnswer까지
    events, done = await sse_events(client, "/chat/stream",
                                    message="gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요",
                                    thread_id="s4")
    types = [e["type"] for e in events]
    assert done and "SubAgent" in types and "FinalAnswer" in types     # 리뷰 A8
    assert types.index("FinalAnswer") < len(types)                   # FinalAnswer 전에 요약 대기 없음 (A11)


@pytest.mark.asyncio
async def test_stream_error_is_reported(client):
    async def boom(*a, **k):
        raise RuntimeError("mcp down")
    srv.app.state.deps.mcp.call_tool = boom
    events, done = await sse_events(client, "/chat/stream", message="auth-service 로그인이 느려요", thread_id="s5")
    assert done and any(e["type"] == "Error" for e in events)         # 리뷰 A3


@pytest.mark.asyncio
async def test_incident_html(client):
    await client.post("/chat", json={"message": "gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요",
                                     "thread_id": "s6"})
    r = await client.get("/incidents", params={"format": "html"})
    assert r.status_code == 200 and "지난 인시던트 리포트" in r.text
    first = (await client.get("/incidents")).json()["incidents"][0]["id"]
    r = await client.get(f"/incidents/{first}", params={"format": "html"})
    assert "<h1>" in r.text and "gateway-service" in r.text
