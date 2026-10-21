"""route / chat_answer — 가벼운 대화는 무거운 파이프라인을 타지 않는다 (Wave 5·12)."""
from langgraph.config import get_stream_writer
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, add_cost, decide_route


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    result, usage = await deps.fast_llm.route(state["query"])
    # 코드 레벨 교정(양방향): 장애 신호가 있으면 incident, 모델이 incident라 해도 아무 신호가 없으면 chat
    route, corrected = decide_route(state["query"], result["route"])
    event = {"type": "RouteDecided", "route": route, "model_route": result["route"],
             "corrected": corrected}
    return {"route": route, "needs_analysis": result["needs_analysis"],
            "cost_usd": add_cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """토큰 단위 스트리밍 — get_stream_writer()는 custom 모드로 소비될 때만 전송된다."""
    writer = get_stream_writer()
    chat_llm = deps.chat_llm or deps.fast_llm   # ollama는 품질 모델(8b)이 대화를 맡는다 — 0.6b 한국어 품질 문제
    reply, usage = await chat_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": add_cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}
