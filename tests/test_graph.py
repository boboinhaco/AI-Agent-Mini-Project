"""그래프 통합 — fake LLM + FakeMCP + 해싱 RAG로 v2 파이프라인을 끝까지 돌린다."""
import pytest
from langgraph.types import Command

from agent.deps import SupervisorDeps
from agent.graph import build_graph
from conftest import run_config


def base_input(q, history=None):
    return {"query": q, "thread_id": "t", "history": history or [], "events": [], "cost_usd": 0.0}


def types(result):
    return [e["type"] for e in result["events"]]


@pytest.mark.asyncio
async def test_chat_route_skips_pipeline(graph):
    r = await graph.ainvoke(base_input("안녕, 너는 누구야?"), config=run_config("chat"))
    assert r["route"] == "chat" and "PlanCreated" not in types(r)


@pytest.mark.asyncio
async def test_incident_pipeline_runs_subagents_and_rag(graph, deps):
    r = await graph.ainvoke(base_input("cache-service 메모리 사용률이 계속 올라가고 있어요"),
                            config=run_config("inc"))
    t = types(r)
    assert r["route"] == "incident"
    assert "ResearchDispatched" in t and "LogsCollected" in t and "ResearchMerged" in t
    runbooks = [e["target"] for e in r["events"] if e["type"] == "TaskCompleted" and e["source"] == "runbook"]
    assert runbooks == ["memory-leak"]                      # RAG가 고른 runbook
    assert ("get_recent_logs", "cache-service") in deps.mcp.calls
    assert r["validation_attempts"] == 2                    # 1차 초안(짧음) 반려 → 2차 합격
    assert "1)" in r["final"] and "cache-service" in r["final"]


@pytest.mark.asyncio
async def test_two_systems_both_investigated(graph):
    r = await graph.ainvoke(base_input("gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요"),
                            config=run_config("two"))
    investigated = {e["target"] for e in r["events"] if e["type"] == "TaskCompleted" and e["source"] == "monitoring"}
    assert {"gateway-service", "payments-service"} <= investigated


@pytest.mark.asyncio
async def test_unknown_system_not_investigated(graph):
    r = await graph.ainvoke(base_input("billing-service 응답이 느려요"), config=run_config("unk"))
    investigated = {e["target"] for e in r["events"] if e["type"] == "TaskCompleted" and e["source"] == "monitoring"}
    assert "billing-service" not in investigated and "auth-service" in investigated


@pytest.mark.asyncio
async def test_hitl_interrupt_and_resume(deps):
    deps_hitl = SupervisorDeps(**{**deps.__dict__, "hitl_enabled": True})
    g = build_graph(deps_hitl)
    cfg = run_config("hitl")
    r = await g.ainvoke(base_input("cache-service 메모리 사용률이 계속 올라가고 있어요"), config=cfg)
    assert "__interrupt__" in r
    payload = r["__interrupt__"][0].value
    assert payload["type"] == "ApprovalRequired" and "서비스/프로세스 재시작" in payload["actions"]
    # 승인 → 검증 반려 → 재작성 → (조치가 또 있으면) 다시 승인 … 승인 요청이 없어질 때까지 재개
    r2, approvals = r, 0
    while r2.get("__interrupt__") and approvals < 4:
        r2 = await g.ainvoke(Command(resume={"approved": True}), config=cfg)
        approvals += 1
    assert not r2.get("__interrupt__") and approvals >= 1
    assert types(r2).count("ApprovalGranted") == approvals and r2["final"]


@pytest.mark.asyncio
async def test_replan_adds_missing_system(deps):
    """계획 단계가 시스템을 빠뜨려도 replan_check가 보충하는지 — plan 결과를 강제로 하나만 남긴다."""
    from agent.nodes.replan import replan_check_node
    state = {"query": "web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘",
             "tasks": [{"id": "task-0", "agent": "research", "kind": "monitoring", "target": "web-01", "status": "done"}],
             "collected": [{"source": "monitoring", "data": {"system_name": "web-01", "status": "healthy", "alerts": []}}],
             "events": [], "replan_rounds": 0}
    out = await replan_check_node(state, deps)
    gap = out["tasks"][1:]
    assert any(t["kind"] == "monitoring" and t["target"] == "auth-service" for t in gap)
    assert any(t["kind"] == "runbook" and t["target"] == "cpu-spike" for t in gap)
