"""validate / finalize — 코드 기준 검증 (Wave 8 강화판).

Before: "30자 이상 + '1)' 포함"만 검사.
After : 아래 4가지를 모두 검사하고, 실패 사유를 모아 write에 피드백으로 돌려보낸다.
  1. 형식    — 30자 이상, 번호 절차 '1)' 포함
  2. 인용    — 조사한 모든 시스템의 이름이 답변에 등장
  3. 수치    — 조사한 시스템의 CPU 또는 메모리 값(원값 또는 반올림 정수)이 답변에 등장
  4. 할루시네이션 — 조사하지 않았고 질문에도 없는 시스템명을 답변이 언급하면 반려
"""
import re

from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, mentioned_systems, system_label

MAX_ATTEMPTS = 3
# \b 대신 명시적 경계 — 한글 조사("billing-service도")가 붙으면 \b가 안 잡힌다
SYSTEM_NAME_RE = re.compile(r"(?<![a-z0-9-])[a-z][a-z0-9]*(?:-[a-z0-9]+)*-(?:service|\d{2})(?![a-z0-9-])")


def _value_cited(draft: str, value: float) -> bool:
    return (str(value) in draft) or (f"{round(value)}%" in draft) or (f"{int(value)}%" in draft)


def _metric_cited(draft: str, d: dict) -> bool:
    """CPU 또는 메모리 중 하나라도 수집값 그대로(또는 반올림) 인용했으면 통과 —
    메모리 누수/디스크 장애 보고에 CPU 수치를 강요하지 않는다."""
    return any(_value_cited(draft, d[k]) for k in ("cpu_percent", "memory_percent") if k in d)


def run_checks(draft: str, query: str, collected: list[dict]) -> list[str]:
    """반려 사유 목록(비어 있으면 합격). 순수 함수 — 단위 테스트 대상."""
    reasons = []
    if len(draft) < 30 or "1)" not in draft:
        reasons.append(f"답변이 너무 짧거나({len(draft)}자) '1) 2) 3)' 번호 형식이 아닙니다.")
    monitored = [c["data"] for c in collected if c.get("source") == "monitoring"]
    investigated = {d["system_name"] for d in monitored}
    missing = [d["system_name"] for d in monitored if d["system_name"] not in draft]
    if missing:
        reasons.append(f"답변에 조사한 시스템 이름({', '.join(missing)})이 빠졌습니다.")
    uncited = [f"{d['system_name']}(CPU {d['cpu_percent']}% / 메모리 {d.get('memory_percent')}%)"
               for d in monitored
               if d["system_name"] in draft and not _metric_cited(draft, d)]
    if uncited:
        reasons.append(f"조회한 수치를 그대로 쓰지 않았습니다: {', '.join(uncited)}.")
    # 사용자 질문에 적힌 이름(목록 밖 이름 포함)은 인용일 뿐 할루시네이션이 아니다
    allowed = investigated | set(mentioned_systems(query)) | set(SYSTEM_NAME_RE.findall(query))
    invented = sorted({m for m in SYSTEM_NAME_RE.findall(draft) if m not in allowed})
    if invented:
        reasons.append(f"조사하지 않은 시스템({', '.join(invented)})을 언급했습니다 — 조회한 사실에 없는 시스템은 쓰면 안 됩니다.")
    return reasons


@traceable(name="validate_node")
async def validate_node(state: AgentState, deps: SupervisorDeps) -> dict:
    draft = state["draft"]
    attempts = state.get("validation_attempts", 0) + 1
    reasons = run_checks(draft, state["query"], state.get("collected", []))
    ok = not reasons
    verdict, partial = ("합격" if ok else "불합격"), False
    if not ok and attempts >= MAX_ATTEMPTS:   # 실모델 무한 재작성 방지 — 통과시키되 '참고용'임을 표시
        ok, verdict, partial = True, "합격(참고용 — 검수 기준 일부 미달)", True
    feedback = None if ok else " ".join(reasons)
    event = {"type": "Validation", "attempt": attempts, "verdict": verdict,
             "partial": partial, "reasons": reasons}
    return {"validation_attempts": attempts, "validation_feedback": feedback,
            "events": state["events"] + [event]}


STATUS_KO = {"healthy": "정상", "degraded": "저하", "down": "중단", "unknown": "미등록"}


def build_header(collected: list[dict]) -> str:
    """코드가 만드는 답변 머리말 — 모델이 시스템 이름을 빼먹어도 '어느 시스템 얘기인지'는 항상 보이게."""
    lines = []
    for c in collected:
        if c.get("source") != "monitoring":
            continue
        d = c["data"]
        lines.append(f"📌 {system_label(d['system_name'])} — 상태 {STATUS_KO.get(d.get('status'), d.get('status'))}"
                     f"({d.get('status')}), CPU {d.get('cpu_percent')}%, 메모리 {d.get('memory_percent')}%"
                     + (f", 알림 {len(d['alerts'])}건" if d.get("alerts") else ""))
    for c in collected:
        if c.get("source") == "runbook":
            title = next((l.lstrip("# ").strip() for l in c["data"].splitlines() if l.startswith("#")), None)
            if title:
                lines.append(f"📖 적용 매뉴얼: {title}")
            break
    return "\n".join(lines)


@traceable(name="finalize_node")
async def finalize_node(state: AgentState, deps: SupervisorDeps) -> dict:
    header = build_header(state.get("collected", []))
    final = (header + "\n\n" + state["draft"]) if header else state["draft"]
    return {"final": final,
            "events": state["events"] + [{"type": "Finalize", "pipeline": "incident-v2"}]}
