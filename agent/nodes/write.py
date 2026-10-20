"""write — 고품질 모델로 초안 작성, 토큰 스트리밍 (Wave 8·10a)."""
from langgraph.config import get_stream_writer
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, add_cost


@traceable(name="write_node")
async def write_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """attempt 번호를 토큰에 실어 보내 재작성 시 프론트가 새 말풍선으로 끊을 수 있게 한다."""
    attempt = state.get("validation_attempts", 0) + 1
    writer = get_stream_writer()
    draft, usage = await deps.quality_llm.write(
        state["query"], state.get("collected", []),
        attempt, state.get("validation_feedback"),
        on_token=lambda piece: writer(
            {"type": "AnswerToken", "attempt": attempt, "delta": piece}))
    return {"draft": draft, "last_prompt": usage.get("prompt"),
            "cost_usd": add_cost(state, usage),
            "events": state["events"] + [{"type": "DraftWritten", "attempt": attempt,
                                          "length": len(draft)}]}
