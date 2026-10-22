"""plan / gate — MCP Prompt 절차에 따라 task를 세우고, 코드 게이트로 걸러낸다 (Wave 4·6)."""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..rag import build_query_text
from ..state import KNOWN_SYSTEMS, SYSTEM_LABELS, AgentState, add_cost, extract_system_name, target_systems


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
        hit = await deps.rag.classify(build_query_text(state["query"], []))
        if hit.get("lexical", 0) == 0:
            # 사용자 문장이 어떤 매뉴얼의 증상과도 겹치지 않는다("알림이 안 가는 것 같아") — 추측해서
            # 엉뚱한 진단을 내는 대신 어느 시스템·어떤 증상인지 되묻고 끝낸다 (ClarificationAsked)
            systems = " · ".join(f"{n}({SYSTEM_LABELS.get(n, '')})" for n in sorted(KNOWN_SYSTEMS))
            final = ("어느 시스템의 어떤 증상인지 조금 더 알려주시면 바로 조사하겠습니다.\n"
                     f"확인할 수 있는 시스템: {systems}\n"
                     "예: 'payments-service 결제가 느려요', '로그 서버 디스크가 부족하대요', "
                     "'게이트웨이에서 타임아웃이 나요'")
            return {"tasks": [], "needs_analysis": False, "clarify": True, "final": final,
                    "cost_usd": add_cost(state, usage),
                    "events": state["events"] + [{"type": "ClarificationAsked",
                                                  "reason": "증상·시스템 불명확", "rag_top": hit["incident_type"]}]}
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
