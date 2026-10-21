"""인시던트 리포트 — 진단이 끝난 run의 타임라인·수집 사실·최종 답변을 Markdown으로 남긴다.

채팅 답변은 흘러가 버리지만 리포트는 incidents 테이블에 남아 /incidents/{id}로 다시 꺼내 볼 수 있다.
"""
from datetime import datetime

EVENT_LABELS = {
    "RouteDecided": lambda e: f"라우팅: {e.get('route')}" + (" (코드 교정)" if e.get("corrected") else ""),
    "PlanCreated": lambda e: f"계획 수립: task {e.get('task_count')}개, 대상 {', '.join(e.get('targets', []))}",
    "GateApplied": lambda e: f"게이트: analysis task {e.get('removed_analysis_tasks')}개 제거" if e.get("removed_analysis_tasks") else None,
    "SystemDiscovery": lambda e: f"시스템명이 없어 전체 {len(e.get('targets', []))}개 시스템 탐색",
    "SystemsScreened": lambda e: f"증상({e.get('incident_type')})과 맞는 시스템 선별: {', '.join(e.get('kept', []))}",
    "ResearchDispatched": lambda e: (lambda a: f"조회 시작: 모니터링 {a.get('metrics', 0)}건, 로그 {a.get('logs', 0)}건, 매뉴얼 {a.get('runbook', 0)}건")(e.get('sub_agents') or {}),
    "TaskCompleted": lambda e: f"{e.get('source')} 조회 완료: {e.get('target')}" + (f" (재시도 {e.get('retries')})" if e.get("retries") else ""),
    "LogsCollected": lambda e: f"로그 수집: {e.get('target')} ({e.get('lines')}줄)",
    "ReplanCheck": lambda e: ("조사 충분" if e.get("is_sufficient") else
                              f"재조사: runbook={e.get('target')}" + (f", 누락 시스템={e.get('missing_systems')}" if e.get("missing_systems") else "")),
    "AnalysisCompleted": lambda e: "패턴 분석 수행",
    "DraftWritten": lambda e: f"초안 작성 (시도 {e.get('attempt')}, {e.get('length')}자)",
    "ApprovalGranted": lambda e: f"운영자 승인: {', '.join(e.get('actions', []))}",
    "ApprovalRejected": lambda e: f"운영자 거절: {', '.join(e.get('actions', []))}",
    "ApprovalEscalated": lambda e: f"거절 상한 — 실행 금지 경고: {', '.join(e.get('actions', []))}",
    "Validation": lambda e: f"검증 {e.get('verdict')}" + (f": {' '.join(e.get('reasons', []))}" if e.get("reasons") else ""),
    "Finalize": lambda e: "최종 답변 확정",
}


def extract_facts(collected: list[dict]) -> tuple[list[str], list[str]]:
    lines, systems = [], []
    for c in collected:
        d = c.get("data")
        if c["source"] == "monitoring":
            systems.append(d["system_name"])
            lines.append(f"- **{d['system_name']}**: 상태 {d['status']}, CPU {d['cpu_percent']}%, "
                         f"메모리 {d['memory_percent']}%, 알림 {len(d.get('alerts', []))}건 "
                         + (" / ".join(d.get("alerts", [])) if d.get("alerts") else ""))
        elif c["source"] == "logs":
            lines.append(f"- 로그 {d['system_name']}: " + " | ".join(d.get("lines", [])[:3]))
        elif c["source"] == "runbook":
            title = next((l.lstrip("# ").strip() for l in d.splitlines() if l.startswith("#")), "runbook")
            lines.append(f"- 적용 runbook: {title}")
        elif c["source"] == "analysis":
            lines.append(f"- 패턴 분석: {d}")
    return lines, systems


def build_report(query: str, result: dict, thread_id: str) -> dict:
    events = result.get("events", [])
    facts, systems = extract_facts(result.get("collected", []))
    incident_type = next((e.get("target") for e in events
                          if e.get("type") == "TaskCompleted" and e.get("source") == "runbook"), None)
    timeline = []
    for e in events:
        fn = EVENT_LABELS.get(e.get("type"))
        label = fn(e) if fn else None
        if label:
            timeline.append(f"- {label}")
    meta = [f"- 스레드: `{thread_id}`", f"- 생성: {datetime.now().isoformat(timespec='seconds')}",
            f"- 장애 유형(runbook): {incident_type or '-'}", f"- 조사 시스템: {', '.join(systems) or '-'}",
            f"- 검증 시도: {result.get('validation_attempts', 0)}회 · 재계획: {result.get('replan_rounds', 0)}회 · "
            f"재시도: {result.get('retry_count', 0)}회 · 토큰 비용 추정치: {result.get('cost_usd', 0)} (실제 과금 아님)"]
    approved, rejected = result.get("approved_actions") or [], result.get("rejected_actions") or []
    if approved:
        meta.append(f"- 운영자가 승인한 조치: {', '.join(approved)}")
    if rejected:
        meta.append(f"- 운영자가 거절한 조치(실행 금지): {', '.join(rejected)}")
    report = "\n".join([
        f"# 인시던트 리포트 — {query}", "", "## 개요", *meta, "",
        "## 타임라인", *timeline, "", "## 수집된 사실", *(facts or ["- (없음)"]), "",
        "## 진단 및 권장 조치", result.get("final", ""), ""])
    return {"incident_type": incident_type, "systems": systems, "report": report}
