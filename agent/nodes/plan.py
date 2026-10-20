"""plan / gate — MCP Prompt 절차에 따라 task를 세우고, 코드 게이트로 걸러낸다 (Wave 4·6)."""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, add_cost, extract_system_name


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
    targets = [t["target"] for t in result["tasks"]
               if t["agent"] == "research" and t.get("target")]
    event = {"type": "PlanCreated", "task_count": len(result["tasks"]), "targets": targets}
    return {"tasks": result["tasks"], "needs_analysis": result["needs_analysis"],
            "cost_usd": add_cost(state, usage), "events": state["events"] + [event]}


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
