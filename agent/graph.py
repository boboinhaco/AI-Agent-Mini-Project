"""v2 Supervisor — langgraph.StateGraph 조립 (노드 구현은 agent/nodes/ 참조).

route → (chat 직행 | incident 파이프라인)
incident: plan → gate → research → replan_check(RAG) → analysis → write → validate → finalize

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다(Wave 13).
"""
from functools import partial

from langgraph.graph import END, StateGraph

from .deps import SupervisorDeps
from .nodes import (analysis_node, chat_answer_node, finalize_node, gate_node, plan_node,
                    replan_check_node, research_node, route_node, validate_node, write_node)
from .state import AgentState

# 하위 호환 — 외부에서 graph.KNOWN_SYSTEMS 등을 참조하던 코드용
from .state import KNOWN_SYSTEMS, extract_system_name as _extract_system_name  # noqa: F401


def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "analysis" if last.get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    return "finalize" if state.get("validation_feedback") is None else "write"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", node(research_node))
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan"})
    g.add_edge("chat_answer", END)
    g.add_edge("plan", "gate")
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"finalize": "finalize", "write": "write"})
    g.add_edge("finalize", END)
    return g.compile()
