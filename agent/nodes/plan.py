"""plan / gate — MCP Prompt 절차에 따라 task를 세우고, 코드 게이트로 걸러낸다 (Wave 4·6)."""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import KNOWN_SYSTEMS, AgentState, add_cost, extract_system_name, target_systems


@traceable(name="plan_node")
async def plan_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """어느 서버가 prompt를 들고 있는지 가정하지 않고 find_prompt()로 찾은 뒤 요청한다."""
    prompt_server = await deps.mcp.find_prompt("incident_analysis_prompt")
    procedure = await deps.mcp.get_prompt(
        "incident_analysis_prompt",
        {"system_name": extract_system_name(state["query"]), "symptom": state["query"]},
        server=prompt_server,
    )
    result, usage = await deps.fast_llm.plan(state["query"], procedure)
    tasks, events, discovery = result["tasks"], [], False
    if not target_systems(state["query"]):   # 시스템명도, '결제' 같은 업무 용어도 없을 때만 전체 탐색
        # 시스템명이 없는 문의("디스크 공간이 부족하다고 알람이 왔어") — 기본 시스템(auth-service)으로
        # 폴백하면 엉뚱한 진단이 나온다(실측). 전체 시스템을 병렬로 훑고 replan_check가 증상과 맞는
        # 시스템만 남긴다(SystemsScreened).
        discovery = True
        others = [t for t in tasks if t["agent"] != "research"]
        tasks = [{"id": f"task-{i}", "agent": "research", "kind": "monitoring",
                  "target": name, "status": "pending"} for i, name in enumerate(sorted(KNOWN_SYSTEMS))] + others
        events.append({"type": "SystemDiscovery", "targets": sorted(KNOWN_SYSTEMS)})
    targets = [t["target"] for t in tasks if t["agent"] == "research" and t.get("target")]
    events.append({"type": "PlanCreated", "task_count": len(tasks), "targets": targets})
    return {"tasks": tasks, "needs_analysis": result["needs_analysis"], "discovery": discovery,
            "cost_usd": add_cost(state, usage), "events": state["events"] + events}


@traceable(name="gate_node")
async def gate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """needs_analysis=False인데 섞여 들어온 analysis task를 프롬프트가 아니라 코드로 제거."""
    tasks = state["tasks"]
    if not state.get("needs_analysis", False):
        kept = [t for t in tasks if t["agent"] != "analysis"]
        removed = len(tasks) - len(kept)
    else:
        kept, removed = tasks, 0
    event = {"type": "GateApplied", "removed_analysis_tasks": removed}
    return {"tasks": kept, "gate_removed": removed, "events": state["events"] + [event]}
