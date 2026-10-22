"""v2 Supervisor — langgraph.StateGraph 조립 (노드 구현은 agent/nodes/ 참조).

route → (chat 직행 | incident 파이프라인)
incident: plan → gate → research(서브그래프: metrics/log/runbook 에이전트) → replan_check(RAG)
          → analysis → write → validate → approval(HITL interrupt, 검증 통과 초안만) → finalize

모든 노드는 SupervisorDeps를 functools.partial로 주입받는 순수 함수다(Wave 13).
"""
from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .deps import SupervisorDeps
from .nodes import (analysis_node, approval_node, chat_answer_node, finalize_node, gate_node,
                    plan_node, replan_check_node, route_node, validate_node, write_node)
from .state import AgentState
from .subgraphs import build_research_subgraph



def _after_route(state: AgentState) -> str:
    return "chat_answer" if state["route"] == "chat" else "plan"


def _after_plan(state: AgentState) -> str:
    """증상·시스템이 불명확해 되물은 경우는 조사 없이 종료."""
    return END if state.get("clarify") else "gate"


def _after_replan_check(state: AgentState) -> str:
    last = state["events"][-1]
    return "analysis" if last.get("is_sufficient") else "research"


def _after_validate(state: AgentState) -> str:
    """불합격이면 재작성, 합격이면 승인 단계로 (폐기될 초안을 승인받지 않는다)."""
    return "approval" if state.get("validation_feedback") is None else "write"


def _after_approval(state: AgentState) -> str:
    """거절되면 피드백을 들고 write로 되돌아간다. 승인·생략·에스컬레이션이면 확정."""
    approval = state.get("approval")
    if approval and not approval.get("approved") and not approval.get("escalated"):
        return "write"
    return "finalize"


def build_graph(deps: SupervisorDeps):
    g = StateGraph(AgentState)
    node = lambda fn: partial(fn, deps=deps)   # 명시적 의존성 주입
    g.add_node("route", node(route_node))
    g.add_node("chat_answer", node(chat_answer_node))
    g.add_node("plan", node(plan_node))
    g.add_node("gate", node(gate_node))
    g.add_node("research", build_research_subgraph(deps))   # compiled subgraph = 노드 하나
    g.add_node("replan_check", node(replan_check_node))
    g.add_node("analysis", node(analysis_node))
    g.add_node("write", node(write_node))
    g.add_node("approval", node(approval_node))
    g.add_node("validate", node(validate_node))
    g.add_node("finalize", node(finalize_node))

    g.set_entry_point("route")
    g.add_conditional_edges("route", _after_route,
                            {"chat_answer": "chat_answer", "plan": "plan"})
    g.add_edge("chat_answer", END)
    g.add_conditional_edges("plan", _after_plan, {"gate": "gate", END: END})
    g.add_edge("gate", "research")
    g.add_edge("research", "replan_check")
    g.add_conditional_edges("replan_check", _after_replan_check,
                            {"analysis": "analysis", "research": "research"})
    g.add_edge("analysis", "write")
    g.add_edge("write", "validate")
    g.add_conditional_edges("validate", _after_validate,
                            {"approval": "approval", "write": "write"})
    g.add_conditional_edges("approval", _after_approval,
                            {"write": "write", "finalize": "finalize"})
    g.add_edge("finalize", END)
    # interrupt()/resume에는 체크포인터가 필요하다. 요청마다 고유 thread_id(run_id)를 쓰므로
    # 메모리 체크포인터로 충분하다(서버 재시작 시 '승인 대기 중'인 run만 사라진다).
    return g.compile(checkpointer=MemorySaver())
