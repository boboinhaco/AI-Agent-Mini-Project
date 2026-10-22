"""plan 결과 정규화 — ollama/openai 어댑터가 공유 (중복 55줄 제거).

모델이 낸 JSON은 형식이 지켜져도 내용이 흔들린다(대상 시스템 오타, task 누락/중복, 언급한 시스템 빠뜨림).
여기서 실행 가능한 task 목록으로 보정한다. 코드가 최종 결정권을 갖는다(W6 정신).
"""
import json

from .state import DEFAULT_SYSTEM, KNOWN_SYSTEMS, target_systems


def normalize_plan(content: str, query: str) -> dict:
    try:
        raw = json.loads(content) if content else {}
    except json.JSONDecodeError:
        raw = {}
    wanted = target_systems(query)   # 명시적 시스템명 또는 업무 용어 별칭(결제→payments-service)
    tasks, seen, idx, analysis_added = [], set(), 0, False
    for t in raw.get("tasks", []) if isinstance(raw, dict) else []:
        agent = t.get("agent")
        if agent == "research":
            target = t.get("target", "")
            if target not in KNOWN_SYSTEMS:          # 오타·목록 밖 이름 → 쿼리에서 다시 찾음
                target = wanted[0] if wanted else DEFAULT_SYSTEM
            if wanted and target not in wanted:      # 질문이 가리킨 시스템이 있으면 모델이 덧붙인 다른 시스템은 제외
                continue                             # (실측: gpt-4o-mini가 auth-service를 임의로 추가 — 누락은 replan이 보충)
            if target in seen:
                continue
            seen.add(target)
            tasks.append({"id": f"task-{idx}", "agent": "research", "kind": "monitoring",
                          "target": target, "status": "pending"})
            idx += 1
        elif agent == "analysis" and not analysis_added:
            analysis_added = True
            tasks.append({"id": "task-analysis", "agent": "analysis",
                          "kind": "pattern-analysis", "status": "pending"})
    # 쿼리가 가리킨 시스템 중 모델이 빠뜨린 것을 보충 (Before 실측: 두 시스템 중 첫 번째만 계획)
    for target in wanted:
        if target not in seen:
            seen.add(target)
            n_research = len([t for t in tasks if t["agent"] == "research"])
            tasks.insert(n_research, {"id": f"task-{idx}", "agent": "research", "kind": "monitoring",
                                      "target": target, "status": "pending"})
            idx += 1
    if not seen:   # research가 하나도 없으면 실행 불능 — 기본 시스템으로 복구 (discovery는 plan_node가 처리)
        tasks.insert(0, {"id": f"task-{idx}", "agent": "research", "kind": "monitoring",
                         "target": DEFAULT_SYSTEM, "status": "pending"})
    return {"needs_analysis": bool(raw.get("needs_analysis", False)) if isinstance(raw, dict) else False,
            "tasks": tasks}
