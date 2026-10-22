"""replan_check — 1차 수집 결과 평가. runbook이 없으면 RAG로 incident_type을 찾아 gap task 추가.

Before: 키워드 규칙(_INCIDENT_TYPE_RULES) + db-down 폴백.
After : deps.rag(RunbookIndex).classify(사용자 질의 + 저하 시스템 알림) — 의미 기반.
"""
from langsmith import traceable

from ..deps import SupervisorDeps
from ..rag import build_query_text
from ..state import AgentState, mentioned_systems, relevant_systems

MAX_ROUNDS = 2   # runbook 보완 1회 + 누락 시스템 보완 1회까지


@traceable(name="replan_check_node")
async def replan_check_node(state: AgentState, deps: SupervisorDeps) -> dict:
    collected = state.get("collected", [])
    rounds = state.get("replan_rounds", 0)
    screened_event = None
    hit = None
    if state.get("discovery") and rounds == 0:
        # 전체 탐색 결과에서 증상과 맞는 시스템만 남긴다 — 증상은 질문만으로 RAG 분류
        hit = await deps.rag.classify(build_query_text(state["query"], []))
        keep = relevant_systems(collected, hit["incident_type"]) or \
            [c["data"]["system_name"] for c in collected if c["source"] == "monitoring"]
        before = {c["data"]["system_name"] for c in collected if c["source"] == "monitoring"}
        collected = [c for c in collected
                     if c["source"] not in ("monitoring", "logs") or c["data"]["system_name"] in keep]
        screened_event = {"type": "SystemsScreened", "incident_type": hit["incident_type"],
                          "kept": keep, "dropped": sorted(before - set(keep))}
    investigated = {c["data"].get("system_name") for c in collected if c["source"] == "monitoring"}
    planned = {t["target"] for t in state["tasks"] if t["kind"] == "monitoring"}
    # (안전망) 질문에 언급됐는데 조사도 계획도 안 된 시스템 — 계획 단계가 빠뜨린 경우
    missing = [s for s in mentioned_systems(state["query"]) if s not in investigated | planned]
    has_runbook = any(c["source"] == "runbook" for c in collected)
    gap_tasks = []
    if rounds < MAX_ROUNDS:
        for i, target in enumerate(missing):
            gap_tasks.append({"id": f"task-gap-sys-{i}", "agent": "research", "kind": "monitoring",
                              "target": target, "status": "pending"})
        all_healthy = bool(collected) and all(
            c["data"].get("status") == "healthy" and not c["data"].get("alerts")
            for c in collected if c["source"] == "monitoring")
        if not has_runbook and all_healthy and hit is None:
            probe = await deps.rag.classify(build_query_text(state["query"], []))
            if probe.get("lexical", 0) == 0:
                # "웹 서버 알람이 안 와" — 증상이 어떤 매뉴얼과도 안 맞고 시스템은 정상: 엉뚱한 매뉴얼 대신
                # '정상' 보고로 넘긴다 (RunbookSkipped)
                has_runbook = True
                screened_event = screened_event or {"type": "RunbookSkipped",
                                                    "reason": "증상 불일치 + 조사 시스템 모두 정상"}
        if not has_runbook:
            if hit is None:   # discovery에서 이미 분류했으면 재사용
                # 사용자 문장만으로 먼저 분류한다 — "메모리가 부족하대"처럼 증상 어휘가 있으면 그대로 쓰고,
                # 없을 때만 저하 시스템의 알림 문구를 보탠다(알림 문구가 사용자 말을 덮던 문제 수정)
                hit = await deps.rag.classify(build_query_text(state["query"], []))
                if hit.get("lexical", 0) == 0:
                    hit = await deps.rag.classify(build_query_text(state["query"], collected))
            gap_tasks.append({"id": "task-gap-runbook", "agent": "research", "kind": "runbook",
                              "target": hit["incident_type"], "status": "pending",
                              "rag": {"title": hit.get("title"), "score": hit.get("score")}})
    extra = [screened_event] if screened_event else []
    update = {"collected": collected} if screened_event else {}
    if not gap_tasks:
        verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": True}
        return {**update, "events": state["events"] + extra + [verdict]}
    runbook = next((t for t in gap_tasks if t["kind"] == "runbook"), None)
    verdict = {"type": "ReplanCheck", "round": rounds, "is_sufficient": False,
               "gap_task": ",".join(t["id"] for t in gap_tasks),
               "missing_systems": missing,
               "target": runbook["target"] if runbook else None,
               "rag_title": runbook["rag"]["title"] if runbook else None,
               "rag_score": runbook["rag"]["score"] if runbook else None}
    return {**update, "tasks": state["tasks"] + gap_tasks, "replan_rounds": rounds + 1,
            "events": state["events"] + extra + [verdict]}
