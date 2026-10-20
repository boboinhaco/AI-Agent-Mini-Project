"""route / chat_answer — 가벼운 대화는 무거운 파이프라인을 타지 않는다 (Wave 5·12)."""
from langgraph.config import get_stream_writer
from langsmith import traceable

from ..deps import SupervisorDeps
from ..state import AgentState, add_cost, is_incident_query


@traceable(name="route_node")
async def route_node(state: AgentState, deps: SupervisorDeps) -> dict:
    result, usage = await deps.fast_llm.route(state["query"])
    # 코드 레벨 교정: 모델이 chat이라 해도 알려진 시스템명이나 장애 표현이 있으면 incident.
    # (소형 모델이 "느려요"를 잡담으로 분류하는 오탐 방지 — 반대 방향은 모델 판단을 존중)
    corrected = False
    if result["route"] == "chat" and is_incident_query(state["query"]):
        result = {**result, "route": "incident"}
        corrected = True
    event = {"type": "RouteDecided", "route": result["route"], "corrected": corrected}
    return {"route": result["route"], "needs_analysis": result["needs_analysis"],
            "cost_usd": add_cost(state, usage),
            "events": state.get("events", []) + [event]}


@traceable(name="chat_answer_node")
async def chat_answer_node(state: AgentState, deps: SupervisorDeps) -> dict:
    """토큰 단위 스트리밍 — get_stream_writer()는 custom 모드로 소비될 때만 전송된다."""
    writer = get_stream_writer()
    reply, usage = await deps.fast_llm.chat(
        state["query"], state.get("history", []),
        on_token=lambda piece: writer({"type": "AnswerToken", "delta": piece}))
    return {"final": reply, "last_prompt": usage.get("prompt"),
            "cost_usd": add_cost(state, usage),
            "events": state["events"] + [{"type": "Finalize", "pipeline": "chat-direct"}]}
