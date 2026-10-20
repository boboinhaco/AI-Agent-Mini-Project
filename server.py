"""all_total 통합 실습 — FastAPI 백엔드 (Wave 1·3·12·14 축 + 전체 조립 + Mini-project 확장).

- Wave 1  : lifespan에서 Agent 번들(MCP 연결·LLM·그래프·RAG 인덱스)을 프로세스당 1회 생성
- Wave 2  : mcp_server.py / mcp_server_docs.py를 subprocess(stdio)로 기동해 연결 유지
- Wave 3  : GET /chat/stream — 그래프 실행 과정을 SSE 이벤트로 스트리밍
- Wave 12 : 대화 이력을 SQLite(session.db)에 저장 — 재시작해도 유지 (+ 요약 메모리)
- Wave 14 : SUPERVISOR_VERSION(v1/v2) 환경변수로 legacy/신형 파이프라인 즉시 전환
- HITL    : 위험 조치가 포함된 초안은 interrupt로 멈추고 /chat/approve(또는 /chat/approve/stream)로 재개
- LangSmith: 요청당 chat_request 루트 run 아래로 노드·LLM·MCP 호출이 계층 기록됨
"""
import json
import os
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()   # LANGCHAIN_TRACING_V2 / API_KEY / PROJECT — import보다 먼저

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel

from agent.deps import SupervisorDeps
from agent.fake_llm import FastLLM, QualityLLM
from agent.graph import build_graph
from agent.mcp_client import MCPClient
from agent.memory import compact_history
from agent.rag import RunbookIndex, make_embeddings
from agent.session_store import SqliteSessionStore
from agent.supervisor_v1 import run_v1

CREATION_COUNT = 0   # Wave 1 — 프로세스 내 Agent 번들 생성 횟수 (항상 1이어야 정상)


class ChatRequest(BaseModel):   # Wave 1 — Pydantic 요청 검증
    message: str
    thread_id: str = "default"


class ApproveRequest(BaseModel):
    thread_id: str = "default"
    approved: bool
    comment: str = ""


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
        quality = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"),
                                   usd_per_1k_tokens=0.005)
        return backend, fast, quality
    fast = FastLLM("fast-model-stub", usd_per_1k_tokens=0.00015)
    quality = QualityLLM("quality-model-stub", usd_per_1k_tokens=0.005)
    return backend, fast, quality


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CREATION_COUNT
    CREATION_COUNT += 1
    mcp = MCPClient()
    await mcp.start()   # Wave 2 — MCP subprocess는 서버 수명 동안 딱 1개
    backend, fast_llm, quality_llm = _build_llms()
    app.state.llm_backend = backend
    rag = RunbookIndex(make_embeddings(backend)).build()   # RAG — runbook 벡터 인덱스 1회 구축
    deps = SupervisorDeps(
        fast_llm=fast_llm,
        quality_llm=quality_llm,
        mcp=mcp,
        session_store=SqliteSessionStore(),
        version=os.environ.get("SUPERVISOR_VERSION", "v2"),
        rag=rag,
        hitl_enabled=os.environ.get("HITL_APPROVAL", "1") != "0",
    )
    app.state.deps = deps
    app.state.graph = build_graph(deps) if deps.version == "v2" else None
    app.state.instance_id = id(deps)
    app.state.pending = {}   # HITL — thread_id → {"run_id", "message"} (승인 대기 중인 run)
    yield
    await mcp.stop()
    deps.session_store.close()
    try:   # 종료 전 LangSmith 배치 전송 플러시 — kill 시 run이 pending으로 남는 것 방지
        from langchain_core.tracers.langchain import wait_for_all_tracers
        wait_for_all_tracers()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "instance_id": app.state.instance_id,
            "creation_count": CREATION_COUNT,
            "supervisor_version": app.state.deps.version,
            "llm_backend": app.state.llm_backend,
            "fast_llm": app.state.deps.fast_llm.name,
            "quality_llm": app.state.deps.quality_llm.name,
            "rag_embeddings": type(app.state.deps.rag.embeddings).__name__,
            "rag_docs": app.state.deps.rag.size,
            "hitl_enabled": app.state.deps.hitl_enabled}


@app.get("/version")
async def version():
    return {"supervisor_version": app.state.deps.version}


@app.get("/session/{thread_id}")
async def session_stats(thread_id: str):
    """요약 메모리 상태 — 원문 메시지 수, 요약된 수, LLM에 실제로 넘어가는 이력 크기."""
    return app.state.deps.session_store.stats(thread_id)


def _metadata(result: dict, run_id: str | None) -> dict:
    return {
        "route": result.get("route"),
        "task_count": len(result.get("tasks", [])),
        "gate_removed": result.get("gate_removed", 0),
        "replan_rounds": result.get("replan_rounds", 0),
        "retry_count": result.get("retry_count", 0),
        "parallel_elapsed": result.get("parallel_elapsed"),
        "validation_attempts": result.get("validation_attempts", 0),
        "approval": result.get("approval"),
        "approval_rejections": result.get("approval_rejections", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "llm_backend": app.state.llm_backend,
        "supervisor_version": app.state.deps.version,
        "instance_id": app.state.instance_id,
        "langsmith_run_id": run_id,
    }


def _run_config(run_id: str) -> dict:
    """체크포인터용 스레드 — 요청(run)마다 고유 id. 세션 thread_id와는 별개다:
    같은 세션의 다음 질문이 이전 run의 상태(events·draft 등)를 이어받지 않게 한다."""
    return {"configurable": {"thread_id": run_id}}


def _interrupt_payload(result: dict) -> dict | None:
    intr = result.get("__interrupt__")
    if not intr:
        return None
    first = intr[0]
    return first.value if hasattr(first, "value") else first


async def _finish_turn(deps, thread_id: str, message: str, final: str) -> dict | None:
    """세션에 턴을 기록하고, 길어졌으면 과거 턴을 요약 압축한다."""
    deps.session_store.append(thread_id, "user", message)
    deps.session_store.append(thread_id, "assistant", final)
    return await compact_history(deps, thread_id)


@traceable(name="chat_request")
async def _handle_chat(message: str, thread_id: str) -> tuple[dict, str | None]:
    deps = app.state.deps
    history = deps.session_store.load(thread_id)   # Wave 12 (+ 요약)
    if deps.version != "v2":
        result = await run_v1(deps, message, thread_id, history)   # Wave 14
        await _finish_turn(deps, thread_id, message, result["final"])
    else:
        run_id = f"{thread_id}:{uuid.uuid4().hex[:8]}"
        result = await app.state.graph.ainvoke(
            {"query": message, "thread_id": thread_id, "history": history,
             "events": [], "cost_usd": 0.0}, config=_run_config(run_id))
        if _interrupt_payload(result):
            app.state.pending[thread_id] = {"run_id": run_id, "message": message}
            result["run_id"] = run_id
        else:
            result["memory"] = await _finish_turn(deps, thread_id, message, result["final"])
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


def _chat_response(result: dict, run_id: str | None) -> dict:
    payload = _interrupt_payload(result)
    if payload:
        return {"reply": None, "status": "approval_required", "approval": payload,
                "run_id": result.get("run_id"), "events": result.get("events", []),
                "metadata": _metadata(result, run_id)}
    return {"reply": result["final"], "status": "done", "events": result.get("events", []),
            "memory": result.get("memory"), "metadata": _metadata(result, run_id)}


@app.post("/chat")
async def chat(req: ChatRequest):
    result, run_id = await _handle_chat(req.message, req.thread_id)
    return _chat_response(result, run_id)


@traceable(name="chat_approve")
async def _handle_approve(req: ApproveRequest) -> tuple[dict, str | None]:
    """HITL — 승인 대기 중인 run을 운영자 결정으로 재개한다."""
    deps = app.state.deps
    pending = app.state.pending.get(req.thread_id)
    if not pending:
        raise HTTPException(404, f"승인 대기 중인 요청이 없습니다: thread_id={req.thread_id}")
    result = await app.state.graph.ainvoke(
        Command(resume={"approved": req.approved, "comment": req.comment}),
        config=_run_config(pending["run_id"]))
    if _interrupt_payload(result):   # 거절 → 재작성 → 또 위험 조치 → 다시 승인 요청
        result["run_id"] = pending["run_id"]
    else:
        app.state.pending.pop(req.thread_id, None)
        result["memory"] = await _finish_turn(deps, req.thread_id, pending["message"], result["final"])
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


@app.post("/chat/approve")
async def chat_approve(req: ApproveRequest):
    result, run_id = await _handle_approve(req)
    return _chat_response(result, run_id)


def _sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _stream_graph(graph_input, run_id: str, thread_id: str, message: str):
    """그래프 실행을 SSE 이벤트로 중계. interrupt(승인 요청)가 나오면 ApprovalRequired를 보내고 멈춘다.

    stream_mode를 리스트로 주면 (mode, payload) 튜플이 나온다 — "updates"는 노드가 끝날 때마다의
    상태 델타, "custom"은 write/chat/서브에이전트가 get_stream_writer()로 즉시 흘려보내는 이벤트다.
    """
    deps = app.state.deps
    seen, final, last_prompt, interrupted = 0, "", None, None
    async for mode, payload in app.state.graph.astream(
            graph_input, config=_run_config(run_id), stream_mode=["updates", "custom"]):
        if mode == "custom":
            yield _sse(payload)
            continue
        if "__interrupt__" in payload:
            interrupted = _interrupt_payload(payload)
            continue
        for node_name, delta in payload.items():
            if not isinstance(delta, dict):
                continue
            for ev in (delta.get("events") or [])[seen:]:
                yield _sse({**ev, "node": node_name})
            if delta.get("events"):
                seen = len(delta["events"])
            if delta.get("final"):
                final = delta["final"]
            if delta.get("last_prompt"):
                last_prompt = delta["last_prompt"]
    if interrupted:
        app.state.pending[thread_id] = {"run_id": run_id, "message": message}
        yield _sse({**interrupted, "run_id": run_id})
    else:
        app.state.pending.pop(thread_id, None)
        yield _sse({"type": "FinalAnswer", "reply": final, "prompt": last_prompt})
        memory = await _finish_turn(deps, thread_id, message, final)
        if memory:
            yield _sse({"type": "MemoryCompacted", **memory})
    yield "data: [DONE]\n\n"


@app.get("/chat/stream")
async def chat_stream(message: str, thread_id: str = "default"):
    """Wave 3 — 그래프의 노드별 진행 상황을 SSE로 스트리밍.

    이벤트마다 'data: {json}\\n\\n' 형태로 내려보내고 마지막에 data: [DONE] 센티널을
    보낸다 — 프론트엔드는 \\n\\n 경계로 이벤트를 분리해 파싱해야 한다(Wave 3b).
    """
    deps = app.state.deps

    async def gen():
        history = deps.session_store.load(thread_id)
        if deps.version != "v2":
            result = await run_v1(deps, message, thread_id, history)
            for ev in result["events"]:
                yield _sse(ev)
            yield _sse({"type": "FinalAnswer", "reply": result["final"]})
            await _finish_turn(deps, thread_id, message, result["final"])
            yield "data: [DONE]\n\n"
            return
        run_id = f"{thread_id}:{uuid.uuid4().hex[:8]}"
        async for chunk in _stream_graph(
                {"query": message, "thread_id": thread_id, "history": history,
                 "events": [], "cost_usd": 0.0}, run_id, thread_id, message):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/chat/approve/stream")
async def chat_approve_stream(thread_id: str = "default", approved: bool = True,
                              comment: str = ""):
    """HITL — 승인/거절로 재개된 이후 과정을 이어서 SSE로 스트리밍."""
    pending = app.state.pending.get(thread_id)
    if not pending:
        raise HTTPException(404, f"승인 대기 중인 요청이 없습니다: thread_id={thread_id}")

    async def gen():
        async for chunk in _stream_graph(
                Command(resume={"approved": approved, "comment": comment}),
                pending["run_id"], thread_id, pending["message"]):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
