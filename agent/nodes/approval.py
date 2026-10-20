"""approval — Human-in-the-loop 승인 노드 (write → approval → validate).

초안에 서비스 재시작·롤백·failover·스케일 변경·삭제 같은 '실행하면 되돌리기 어려운 조치'가
포함돼 있으면 LangGraph interrupt()로 실행을 멈추고 운영자 승인을 기다린다.
  - 승인  → validate로 진행 (ApprovalGranted)
  - 거절  → 거절 사유를 피드백으로 write에 돌려보내 해당 조치를 뺀 안으로 재작성 (ApprovalRejected)
interrupt는 체크포인터(MemorySaver)가 있어야 동작하며, 서버는 Command(resume=...)로 재개한다.
"""
import re

from langgraph.types import interrupt
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState

RISKY_ACTIONS = [
    (r"재시작|restart", "서비스/프로세스 재시작"),
    (r"롤백|rollback", "배포 롤백"),
    (r"failover|페일오버|노드로 전환", "failover 전환"),
    (r"오토스케일링|스케일\s*(아웃|업)|scale\s*(out|up)|한 대 더 띄워", "인스턴스 증설(스케일 아웃)"),
    (r"로드밸런서에서\s*(일시\s*)?제외", "로드밸런서에서 인스턴스 제외"),
    (r"볼륨을?\s*확장|스토리지를?\s*연결", "스토리지 볼륨 확장"),
    (r"삭제|지우|정리해", "파일/데이터 삭제"),
]
MAX_REJECTIONS = 2


def detect_risky_actions(draft: str) -> list[str]:
    found = []
    for pattern, label in RISKY_ACTIONS:
        if re.search(pattern, draft, re.IGNORECASE) and label not in found:
            found.append(label)
    return found


@traceable(name="approval_node")
async def approval_node(state: AgentState, deps: SupervisorDeps) -> dict:
    draft = state["draft"]
    actions = detect_risky_actions(draft)
    if not actions or not deps.hitl_enabled:
        return {"approval": None,
                "events": state["events"] + [{"type": "ApprovalSkipped",
                                              "reason": "위험 조치 없음" if not actions
                                              else "HITL 비활성"}]}
    rejections = state.get("approval_rejections", 0)
    if rejections >= MAX_REJECTIONS:
        # 거절 상한 — 모델이 계속 같은 조치를 넣는다면 더 묻지 않고, 거절된 조치는 실행 금지라는
        # 경고를 답변에 명시한 채 검수로 넘긴다(무한 승인 루프 방지).
        caveat = (f"\n\n※ 운영자가 거절한 조치({', '.join(actions)})는 실행하지 마세요 — "
                  f"조사·확인 단계까지만 진행하고 조치는 별도 승인 후 수행합니다.")
        return {"draft": draft + caveat,
                "approval": {"approved": False, "actions": actions, "escalated": True},
                "events": state["events"] + [{"type": "ApprovalEscalated", "actions": actions,
                                              "rejections": rejections}]}
    decision = interrupt({   # ← 여기서 그래프가 멈추고, 서버가 운영자 응답으로 resume한다
        "type": "ApprovalRequired", "actions": actions, "draft": draft,
        "attempt": rejections + 1,
    })
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    comment = (decision.get("comment") or "") if isinstance(decision, dict) else ""
    if approved:
        return {"approval": {"approved": True, "actions": actions, "comment": comment},
                "events": state["events"] + [{"type": "ApprovalGranted", "actions": actions}]}
    rejections += 1
    feedback = (f"운영자가 다음 조치를 거절했습니다: {', '.join(actions)}. "
                f"해당 조치를 제외하고 조사·확인 중심의 보수적인 대응안으로 다시 작성하세요."
                + (f" 운영자 코멘트: {comment}" if comment else ""))
    return {"approval": {"approved": False, "actions": actions, "comment": comment},
            "approval_rejections": rejections,
            "validation_feedback": feedback,
            "events": state["events"] + [{"type": "ApprovalRejected", "actions": actions,
                                          "rejections": rejections}]}
