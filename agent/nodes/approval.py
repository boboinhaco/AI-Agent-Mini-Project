"""approval — Human-in-the-loop 승인 노드 (write → validate → approval → finalize).

검증을 통과한 초안에 재시작·롤백·failover·스케일 변경·삭제 같은 '되돌리기 어려운 조치'가 있으면
LangGraph interrupt()로 멈추고 운영자 결정을 기다린다.
  - 승인  → 조치를 approved_actions에 기억하고 finalize (같은 run에서 다시 묻지 않음)
  - 거절  → rejected_actions에 기억, "그 조치와 그 단어를 쓰지 말라"는 피드백으로 write에 되돌림
  - 거절한 조치가 재작성에 또 나오면 → 더 묻지 않고 '실행 금지' 경고를 달아 종료 (ApprovalEscalated)
감지 시 "재시작은 안 돼요", "롤백 금지" 같은 부정 문맥의 문장은 제외한다.
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
NEGATION = re.compile(r"안\s*돼|안\s*됨|안\s*된|금지|하지\s*마|하지\s*않|않는다|않고|말\s*것|거부|거절|제외|불가|대신")
MAX_REJECTIONS = 2


def _sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?다요])\s+|\n+|\s*\d\)\s*", text) if x.strip()]


def detect_risky_actions(draft: str) -> list[str]:
    """부정 문맥 문장("재시작은 안 돼요", "롤백 금지")을 뺀 나머지에서 위험 조치를 찾는다."""
    positive = " ".join(s for s in _sentences(draft) if not NEGATION.search(s))
    found = []
    for pattern, label in RISKY_ACTIONS:
        if re.search(pattern, positive, re.IGNORECASE) and label not in found:
            found.append(label)
    return found


def _caveat(actions: list[str]) -> str:
    return (f"\n\n※ 운영자가 거절한 조치({', '.join(actions)})는 실행하지 마세요 — "
            f"조사·확인 단계까지만 진행하고 조치는 별도 승인 후 수행합니다.")


@traceable(name="approval_node")
async def approval_node(state: AgentState, deps: SupervisorDeps) -> dict:
    draft = state["draft"]
    actions = detect_risky_actions(draft)
    approved = list(state.get("approved_actions", []))
    rejected = list(state.get("rejected_actions", []))
    rejections = state.get("approval_rejections", 0)
    if not actions or not deps.hitl_enabled:
        return {"approval": None,
                "events": state["events"] + [{"type": "ApprovalSkipped",
                                              "reason": "위험 조치 없음" if not actions else "HITL 비활성"}]}
    reused = [a for a in actions if a in rejected]
    if reused or rejections >= MAX_REJECTIONS:
        # 거절한 조치를 모델이 또 넣었다 — 더 묻지 않고 '실행 금지' 경고를 달아 종료 (승인 피로·무한 루프 방지)
        return {"draft": draft + _caveat(reused or actions),
                "approval": {"approved": False, "actions": actions, "escalated": True,
                             "rejected_actions": rejected},
                "events": state["events"] + [{"type": "ApprovalEscalated", "actions": reused or actions,
                                              "rejections": rejections}]}
    pending = [a for a in actions if a not in approved]
    if not pending:
        return {"approval": {"approved": True, "actions": actions, "already": True},
                "events": state["events"] + [{"type": "ApprovalSkipped",
                                              "reason": f"이미 승인된 조치: {', '.join(actions)}"}]}
    decision = interrupt({   # ← 여기서 그래프가 멈추고, 서버가 운영자 응답으로 resume한다
        "type": "ApprovalRequired", "actions": pending, "draft": draft,
        "attempt": rejections + 1, "note": "승인해도 에이전트가 직접 실행하지는 않습니다 — 답변에 포함할지만 결정합니다.",
    })
    ok = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    comment = (decision.get("comment") or "") if isinstance(decision, dict) else ""
    if ok:
        return {"approval": {"approved": True, "actions": pending, "comment": comment},
                "approved_actions": approved + pending,
                "events": state["events"] + [{"type": "ApprovalGranted", "actions": pending}]}
    rejections += 1
    feedback = (f"운영자가 다음 조치를 거절했습니다: {', '.join(pending)}. "
                f"이 조치를 넣지 말고, '재시작', '롤백' 같은 해당 단어 자체를 쓰지 마세요. "
                f"조사·확인·담당팀 에스컬레이션 중심의 보수적인 대응안으로 다시 작성하세요."
                + (f" 운영자 코멘트: {comment}" if comment else ""))
    return {"approval": {"approved": False, "actions": pending, "comment": comment},
            "approval_rejections": rejections, "rejected_actions": rejected + pending,
            "validation_feedback": feedback,
            "events": state["events"] + [{"type": "ApprovalRejected", "actions": pending,
                                          "rejections": rejections, "comment": comment}]}
