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
from agent.report import build_report
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
        chat_llm=quality_llm if backend == "ollama" else None,   # 로컬 0.6b는 대화 품질이 낮아 8b가 대화 담당
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


_HTML_CSS = ("body{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:860px;margin:32px auto;"
             "padding:0 16px;color:#1d2030;line-height:1.6}h1{font-size:20px}h2{font-size:16px;margin-top:24px}"
             "table{border-collapse:collapse;width:100%}th,td{border:1px solid #c9cdd8;padding:6px 10px;text-align:left}"
             "a{color:#5b5bd6}code{background:#f1f2f7;padding:1px 4px}")


def _html_page(title: str, body: str) -> str:
    from fastapi.responses import HTMLResponse
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
                        f"<style>{_HTML_CSS}</style></head><body>{body}</body></html>")


@app.get("/incidents")
async def list_incidents(limit: int = 50, format: str = "json"):
    """저장된 인시던트 리포트 목록 (최신순). format=html 이면 사람이 보는 목록 페이지."""
    items = app.state.deps.session_store.list_incidents(limit)
    if format != "html":
        return {"incidents": items}
    rows = "".join(f"<tr><td><a href='/incidents/{i['id']}?format=html'>#{i['id']}</a></td><td>{i['created']}</td>"
                   f"<td>{i['query']}</td><td>{i['incident_type'] or '-'}</td><td>{', '.join(i['systems'])}</td></tr>"
                   for i in items)
    return _html_page("지난 인시던트 리포트", f"<h1>지난 인시던트 리포트</h1><table><tr><th>번호</th><th>생성</th>"
                      f"<th>질문</th><th>장애 유형</th><th>시스템</th></tr>{rows or '<tr><td colspan=5>없음</td></tr>'}</table>")


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: int, format: str = "json"):
    """인시던트 리포트 1건 — format=md 는 Markdown 원문, format=html 은 읽기용 페이지."""
    rep = app.state.deps.session_store.get_incident(incident_id)
    if not rep:
        raise HTTPException(404, f"incident {incident_id} 없음")
    if format == "md":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(rep["report"], media_type="text/markdown; charset=utf-8")
    if format == "html":
        import markdown
        body = markdown.markdown(rep["report"], extensions=["tables"])
        return _html_page(f"인시던트 리포트 #{incident_id}",
                          f"<p><a href='/incidents?format=html'>← 지난 리포트 목록</a></p>{body}")
    return rep


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


async def _discard_pending(thread_id: str, reason: str) -> dict | None:
    """승인 대기 중인 run이 있으면 폐기(리뷰 A1) — 체크포인트도 지워 메모리 누수 방지(리뷰 A4)."""
    pending = app.state.pending.pop(thread_id, None)
    if not pending:
        return None
    await _drop_run(pending["run_id"])
    deps = app.state.deps
    if pending.get("user_saved"):   # 질문은 이미 저장됨 — 어떤 결말이었는지 기록해 이력 공백을 막는다
        deps.session_store.append(thread_id, "assistant",
                                  f"[승인 대기 중이던 요청이 {reason}로 종료되었습니다]")
    return {"type": "PendingDiscarded", "run_id": pending["run_id"], "reason": reason}


async def _drop_run(run_id: str) -> None:
    try:
        await app.state.graph.checkpointer.adelete_thread(run_id)
    except Exception:   # 체크포인터 구현에 따라 없을 수 있음 — 정리 실패는 치명적이지 않다
        pass


def _interrupt_payload(result: dict) -> dict | None:
    intr = result.get("__interrupt__")
    if not intr:
        return None
    first = intr[0]
    return first.value if hasattr(first, "value") else first


async def _finish_turn(deps, thread_id: str, message: str, final: str,
                       result: dict | None = None, user_saved: bool = False) -> dict | None:
    """세션에 턴을 기록하고, 장애 진단이었으면 리포트를 저장하고, 길어졌으면 과거 턴을 요약 압축한다."""
    if not user_saved:
        deps.session_store.append(thread_id, "user", message)
    deps.session_store.append(thread_id, "assistant", final)
    if result is not None and result.get("route") == "incident":
        rep = build_report(message, result, thread_id)
        result["incident_id"] = deps.session_store.save_incident(
            thread_id, message, rep["incident_type"], rep["systems"], rep["report"])
    return await compact_history(deps, thread_id)


@traceable(name="chat_request")
async def _handle_chat(message: str, thread_id: str) -> tuple[dict, str | None]:
    deps = app.state.deps
    history = deps.session_store.load(thread_id)   # Wave 12 (+ 요약)
    if deps.version != "v2":
        result = await run_v1(deps, message, thread_id, history)   # Wave 14
        await _finish_turn(deps, thread_id, message, result["final"])
    else:
        discarded = await _discard_pending(thread_id, "새 질문")
        run_id = f"{thread_id}:{uuid.uuid4().hex[:8]}"
        try:
            result = await app.state.graph.ainvoke(
                {"query": message, "thread_id": thread_id, "history": history,
                 "events": [], "cost_usd": 0.0}, config=_run_config(run_id))
        except Exception:
            await _drop_run(run_id)
            raise
        if discarded:
            result["events"] = [discarded] + list(result.get("events", []))
        if _interrupt_payload(result):
            # 승인 대기 — 질문을 지금 저장해 서버 재시작 시 이력 공백을 막는다 (리뷰 A2)
            deps.session_store.append(thread_id, "user", message)
            app.state.pending[thread_id] = {"run_id": run_id, "message": message, "user_saved": True}
            result["run_id"] = run_id
        else:
            result["memory"] = await _finish_turn(deps, thread_id, message, result["final"], result)
            await _drop_run(run_id)
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


def _chat_response(result: dict, run_id: str | None) -> dict:
    payload = _interrupt_payload(result)
    if payload:
        return {"reply": None, "status": "approval_required", "approval": payload,
                "run_id": result.get("run_id"), "events": result.get("events", []),
                "metadata": _metadata(result, run_id)}
    return {"reply": result["final"], "status": "done", "events": result.get("events", []),
            "memory": result.get("memory"), "incident_id": result.get("incident_id"),
            "facts": [c["data"] for c in result.get("collected", []) if c["source"] == "monitoring"],
            "logs": [c["data"] for c in result.get("collected", []) if c["source"] == "logs"],
            "metadata": _metadata(result, run_id)}


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
    try:
        result = await app.state.graph.ainvoke(
            Command(resume={"approved": req.approved, "comment": req.comment}),
            config=_run_config(pending["run_id"]))
    except Exception:
        app.state.pending.pop(req.thread_id, None)
        await _drop_run(pending["run_id"])
        raise
    if _interrupt_payload(result):   # 거절 → 재작성 → 다른 위험 조치 → 다시 승인 요청
        result["run_id"] = pending["run_id"]
    else:
        if app.state.pending.get(req.thread_id, {}).get("run_id") == pending["run_id"]:
            app.state.pending.pop(req.thread_id, None)
        result["memory"] = await _finish_turn(deps, req.thread_id, pending["message"], result["final"],
                                              result, user_saved=pending.get("user_saved", False))
        await _drop_run(pending["run_id"])
    rt = get_current_run_tree()
    return result, (str(rt.id) if rt else None)


@app.post("/chat/approve")
async def chat_approve(req: ApproveRequest):
    result, run_id = await _handle_approve(req)
    return _chat_response(result, run_id)


def _sse(obj) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _stream_graph(graph_input, run_id: str, thread_id: str, message: str,
                        pending: dict | None = None):
    """그래프 실행을 SSE 이벤트로 중계. interrupt(승인 요청)가 나오면 ApprovalRequired를 보내고 멈춘다.

    - stream_mode ["updates","custom"] + subgraphs=True: 서브에이전트의 custom 이벤트(SubAgent)까지 받는다.
      서브그래프 내부 노드의 updates는 네임스페이스가 붙어 오므로 최상위(네임스페이스 없음) 델타만 이벤트로 쓴다.
    - 예외는 Error 이벤트로 전달하고 [DONE]은 항상 보낸다 (리뷰 A3).
    """
    deps = app.state.deps
    seen, final, last_prompt, interrupted = 0, "", None, None
    last_state: dict = {}
    if isinstance(graph_input, Command):   # 승인 후 재개 — 이미 보낸 이벤트는 건너뛴다
        snapshot = await app.state.graph.aget_state(_run_config(run_id))
        seen = len((snapshot.values or {}).get("events", []))
        last_state.update(snapshot.values or {})
    try:
        async for item in app.state.graph.astream(
                graph_input, config=_run_config(run_id), stream_mode=["updates", "custom"],
                subgraphs=True):
            namespace, mode, payload = item
            if mode == "custom":
                yield _sse(payload)
                continue
            if namespace:   # 서브그래프 내부 노드의 상태 델타 — 최상위 research 델타에 합쳐져 다시 온다
                continue
            if "__interrupt__" in payload:
                interrupted = _interrupt_payload(payload)
                continue
            for node_name, delta in payload.items():
                if not isinstance(delta, dict):
                    continue
                last_state.update(delta)
                for ev in (delta.get("events") or [])[seen:]:
                    yield _sse({**ev, "node": node_name})
                if delta.get("events"):
                    seen = len(delta["events"])
                if delta.get("final"):
                    final = delta["final"]
                if delta.get("last_prompt"):
                    last_prompt = delta["last_prompt"]
    except Exception as e:   # 모델·MCP 오류 — 사용자에게 알리고 run을 정리한다
        yield _sse({"type": "Error", "message": f"처리 중 오류가 발생했습니다 ({type(e).__name__}). "
                                                 "잠시 후 같은 질문을 다시 보내 주세요.", "detail": str(e)[:200]})
        app.state.pending.pop(thread_id, None)
        await _drop_run(run_id)
        yield "data: [DONE]\n\n"
        return
    if interrupted:
        if not (pending and pending.get("user_saved")):
            deps.session_store.append(thread_id, "user", message)   # 승인 대기 중 재시작 대비 (리뷰 A2)
        app.state.pending[thread_id] = {"run_id": run_id, "message": message, "user_saved": True}
        yield _sse({**interrupted, "run_id": run_id})
    else:
        if app.state.pending.get(thread_id, {}).get("run_id") == run_id:
            app.state.pending.pop(thread_id, None)
        result = {**last_state, "final": final}
        # 답은 먼저 보여주고(리뷰 A11), 리포트 저장·요약 압축은 그 뒤에
        user_saved = bool(pending and pending.get("user_saved"))
        if not user_saved:
            deps.session_store.append(thread_id, "user", message)
        deps.session_store.append(thread_id, "assistant", final)
        incident_id = None
        if result.get("route") == "incident":
            rep = build_report(message, result, thread_id)
            incident_id = deps.session_store.save_incident(
                thread_id, message, rep["incident_type"], rep["systems"], rep["report"])
        yield _sse({"type": "FinalAnswer", "reply": final, "prompt": last_prompt, "incident_id": incident_id})
        memory = await compact_history(deps, thread_id)
        if memory:
            yield _sse({"type": "MemoryCompacted", **memory})
        await _drop_run(run_id)
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
        discarded = await _discard_pending(thread_id, "새 질문")
        if discarded:
            yield _sse(discarded)
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
                pending["run_id"], thread_id, pending["message"], pending=pending):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
