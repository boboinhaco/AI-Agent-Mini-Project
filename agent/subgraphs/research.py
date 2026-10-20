"""Research 서브그래프 — 단일 research_node를 전문 서브에이전트 3개로 분리 (멀티 에이전트화).

    dispatch ─┬─ metrics_agent  (get_monitoring_status Tool, 시스템별 병렬 + 재시도)
              ├─ log_agent      (get_recent_logs Tool, 시스템별 병렬)
              └─ runbook_agent  (runbook Resource — 어느 서버가 제공하는지 먼저 탐색)
                    └──────────── merge (task 상태 갱신·소요시간·이벤트 합산)

세 에이전트는 같은 superstep에서 동시에 실행되며, 각자 자기 결과만 Annotated[list, add]
채널에 "추가"한다 — 공유 키(tasks 등)를 동시에 덮어쓰지 않도록 merge가 한 번에 정리한다.
메인 그래프에는 compiled subgraph 하나가 "research" 노드로 꽂힌다(supervisor-of-supervisors).
"""
import asyncio
import operator
import time
from functools import partial
from typing import Annotated, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langsmith import traceable

from ..deps import SupervisorDeps
from ..retry import with_retry

TASK_DELAY = 0.35   # 태스크당 고정 지연 — 병렬 효과를 실측하기 위함 (기존 research_node와 동일)


class ResearchState(TypedDict, total=False):
    # 부모(AgentState)와 공유하는 키
    tasks: list[dict]
    collected: Annotated[list[dict], operator.add]
    events: Annotated[list[dict], operator.add]
    retry_count: int
    parallel_elapsed: float
    # 서브그래프 내부 전용
    research_started: float
    done_ids: Annotated[list[str], operator.add]
    retry_added: Annotated[int, operator.add]
    agents_run: Annotated[list[str], operator.add]


def _pending(tasks: list[dict], kind: str) -> list[dict]:
    return [t for t in tasks if t["agent"] == "research" and t["status"] == "pending"
            and t["kind"] == kind]


def _emit(agent: str, status: str, **extra):
    try:
        get_stream_writer()({"type": "SubAgent", "agent": agent, "status": status, **extra})
    except Exception:   # 스트리밍 컨텍스트가 아니면 무시
        pass


@traceable(name="research.dispatch")
async def dispatch(state: ResearchState, deps: SupervisorDeps) -> dict:
    tasks = state["tasks"]
    counts = {"metrics": len(_pending(tasks, "monitoring")),
              "logs": len(_pending(tasks, "monitoring")),
              "runbook": len(_pending(tasks, "runbook"))}
    return {"research_started": time.perf_counter(),
            "events": [{"type": "ResearchDispatched", "sub_agents": counts}]}


@traceable(name="research.metrics_agent")
async def metrics_agent(state: ResearchState, deps: SupervisorDeps) -> dict:
    todo = _pending(state["tasks"], "monitoring")
    if not todo:
        return {"agents_run": ["metrics:skip"]}
    _emit("metrics_agent", "started", targets=[t["target"] for t in todo])

    async def one(task):
        await asyncio.sleep(TASK_DELAY)
        data, retries = await with_retry(
            deps.mcp.call_tool, "get_monitoring_status", {"system_name": task["target"]})
        return task, data, retries

    results = await asyncio.gather(*(one(t) for t in todo))
    collected, events, done, retries_total = [], [], [], 0
    for task, data, retries in results:
        collected.append({"source": "monitoring", "data": data})
        done.append(task["id"]); retries_total += retries
        events.append({"type": "TaskCompleted", "id": task["id"], "source": "monitoring",
                       "target": task["target"], "retries": retries, "agent": "metrics_agent"})
    _emit("metrics_agent", "done", count=len(done))
    return {"collected": collected, "events": events, "done_ids": done,
            "retry_added": retries_total, "agents_run": ["metrics"]}


@traceable(name="research.log_agent")
async def log_agent(state: ResearchState, deps: SupervisorDeps) -> dict:
    """모니터링 대상 시스템마다 최근 로그를 함께 가져온다 — 수치만으로는 안 보이는
    '배포 직후', '배치 중복 실행' 같은 원인 단서를 write 단계에 공급한다."""
    todo = _pending(state["tasks"], "monitoring")
    if not todo:
        return {"agents_run": ["logs:skip"]}
    _emit("log_agent", "started", targets=[t["target"] for t in todo])

    async def one(task):
        await asyncio.sleep(TASK_DELAY)
        data = await deps.mcp.call_tool("get_recent_logs",
                                        {"system_name": task["target"], "limit": 5})
        return task, data

    results = await asyncio.gather(*(one(t) for t in todo))
    collected, events = [], []
    for task, data in results:
        collected.append({"source": "logs", "data": data})
        events.append({"type": "LogsCollected", "target": task["target"],
                       "lines": len(data.get("lines", [])), "agent": "log_agent"})
    _emit("log_agent", "done", count=len(results))
    return {"collected": collected, "events": events, "agents_run": ["logs"]}


@traceable(name="research.runbook_agent")
async def runbook_agent(state: ResearchState, deps: SupervisorDeps) -> dict:
    todo = _pending(state["tasks"], "runbook")
    if not todo:
        return {"agents_run": ["runbook:skip"]}
    _emit("runbook_agent", "started", targets=[t["target"] for t in todo])
    # URI를 하드코딩하지 않고, 어느 MCP 서버가 runbook resource를 제공하는지 먼저 찾는다
    server, uri_template = await deps.mcp.find_resource_template("get_incident_runbook")

    async def one(task):
        await asyncio.sleep(TASK_DELAY)
        uri = uri_template.format(incident_type=task["target"])
        return task, await deps.mcp.read_resource(uri, server=server)

    results = await asyncio.gather(*(one(t) for t in todo))
    collected, events, done = [], [], []
    for task, text in results:
        collected.append({"source": "runbook", "data": text})
        done.append(task["id"])
        events.append({"type": "TaskCompleted", "id": task["id"], "source": "runbook",
                       "target": task["target"], "retries": 0, "agent": "runbook_agent"})
    _emit("runbook_agent", "done", count=len(done))
    return {"collected": collected, "events": events, "done_ids": done,
            "agents_run": ["runbook"]}


@traceable(name="research.merge")
async def merge(state: ResearchState, deps: SupervisorDeps) -> dict:
    done = set(state.get("done_ids", []))
    tasks = [{**t, "status": "done"} if t["id"] in done else dict(t) for t in state["tasks"]]
    elapsed = round(time.perf_counter() - state.get("research_started", time.perf_counter()), 2)
    return {"tasks": tasks,
            "retry_count": state.get("retry_count", 0) + state.get("retry_added", 0),
            "parallel_elapsed": elapsed,
            "events": [{"type": "ResearchMerged", "done": len(done), "elapsed": elapsed,
                        "agents": state.get("agents_run", [])}]}


def build_research_subgraph(deps: SupervisorDeps):
    g = StateGraph(ResearchState)
    node = lambda fn: partial(fn, deps=deps)
    g.add_node("dispatch", node(dispatch))
    g.add_node("metrics_agent", node(metrics_agent))
    g.add_node("log_agent", node(log_agent))
    g.add_node("runbook_agent", node(runbook_agent))
    g.add_node("merge", node(merge))
    g.set_entry_point("dispatch")
    for agent in ("metrics_agent", "log_agent", "runbook_agent"):   # fan-out (같은 superstep)
        g.add_edge("dispatch", agent)
        g.add_edge(agent, "merge")                                  # fan-in
    g.add_edge("merge", END)
    return g.compile()
