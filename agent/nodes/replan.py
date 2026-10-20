"""replan_check — 1차 수집 결과 평가. runbook이 없으면 RAG로 incident_type을 찾아 gap task 추가.

Before: 키워드 규칙(_INCIDENT_TYPE_RULES) + db-down 폴백.
After : deps.rag(RunbookIndex).classify(사용자 질의 + 저하 시스템 알림) — 의미 기반.
"""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..rag import build_query_text
from ..state import AgentState


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    collected = state.get("collected", [])
    has_runbook = any(c["source"] == "runbook" for c in collected)
    rounds = state.get("replan_rounds", 0)
    if has_runbook or rounds >= 1:
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True}
        return {"events": state["events"] + [verdict]}

    hit = await deps.rag.classify(build_query_text(state["query"], collected))
    incident_type = hit["incident_type"]
    gap_task = {"id": "task-gap-runbook", "agent": "research", "kind": "runbook",
                "target": incident_type, "status": "pending"}
    verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
               "gap_task": gap_task["id"], "target": incident_type,
               "rag_title": hit.get("title"), "rag_score": hit.get("score")}
    return {"tasks": state["tasks"] + [gap_task], "replan_rounds": rounds + 1,
            "events": state["events"] + [verdict]}
