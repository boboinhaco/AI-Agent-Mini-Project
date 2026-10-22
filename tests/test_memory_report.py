"""요약 메모리와 인시던트 리포트 저장."""
import pytest

from agent import memory
from agent.memory import compact_history
from agent.report import build_report


@pytest.mark.asyncio
async def test_compact_history_summarizes_and_keeps_name(deps, monkeypatch):
    monkeypatch.setattr(memory, "THRESHOLD", 4)
    monkeypatch.setattr(memory, "KEEP_RECENT", 2)
    store = deps.session_store
    store.append("m", "user", "제 이름은 민수입니다"); store.append("m", "assistant", "안녕하세요 민수님")
    store.append("m", "user", "오늘 날씨 어때?");  store.append("m", "assistant", "맑아요")
    store.append("m", "user", "고마워");           store.append("m", "assistant", "천만에요")
    assert await compact_history(deps, "m") is not None
    history = store.load("m")
    assert history[0]["role"] == "system" and "민수" in history[0]["content"]
    assert len(history) == 1 + 2
    reply, _ = await deps.fast_llm.chat("제 이름이 뭐였죠?", history)
    assert "민수" in reply


def test_build_and_store_report(deps):
    result = {"route": "incident", "final": "1) ... 2) ... 3) ...", "validation_attempts": 2,
              "collected": [{"source": "monitoring", "data": {"system_name": "log-service", "status": "degraded",
                                                               "cpu_percent": 20.0, "memory_percent": 35.0,
                                                               "alerts": ["disk usage 95%"]}},
                            {"source": "runbook", "data": "# Runbook: 디스크 용량 부족 대응 절차\n1. x"}],
              "events": [{"type": "RouteDecided", "route": "incident"},
                         {"type": "TaskCompleted", "source": "runbook", "target": "disk-full"},
                         {"type": "Validation", "verdict": "합격", "reasons": []}]}
    result["approved_actions"] = ["파일/데이터 삭제"]; result["rejected_actions"] = ["서비스/프로세스 재시작"]
    rep = build_report("log-service 디스크 부족", result, "t")
    assert rep["incident_type"] == "disk-full" and rep["systems"] == ["log-service"]
    assert "거절한 조치(실행 금지): 서비스/프로세스 재시작" in rep["report"] and "실제 과금 아님" in rep["report"]
    assert "## 타임라인" in rep["report"] and "disk usage 95%" in rep["report"]
    iid = deps.session_store.save_incident("t", "q", rep["incident_type"], rep["systems"], rep["report"])
    assert deps.session_store.get_incident(iid)["incident_type"] == "disk-full"
    assert deps.session_store.list_incidents()[0]["id"] == iid


def test_chat_prompt_keeps_summary_when_history_is_long():
    from agent.prompts import chat_user
    history = [{"role": "system", "content": "[이전 대화 요약] 사용자 이름: 홍길동"}] + \
              [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(16)]
    rendered = chat_user("내 이름이 뭐야", history)
    assert "사용자 이름: 홍길동" in rendered and "m15" in rendered     # 요약은 항상, 최근 메시지는 뒤에서


@pytest.mark.asyncio
async def test_connection_pool_query_picks_latency_runbook():
    from agent.rag import HashEmbeddings, RunbookIndex
    idx = RunbookIndex(HashEmbeddings()).build()
    assert (await idx.classify("인증 서비스 커넥션 풀 확인해줘"))["incident_type"] == "high-latency"
    assert (await idx.classify("로그인 시에 바로 로그인이 되지 않고 좀 오래 기다려야해"))["incident_type"] == "high-latency"


@pytest.mark.asyncio
async def test_healthy_system_with_unmatched_symptom_skips_runbook(graph):
    r = await graph.ainvoke({"query": "웹 서버에서 알람이 안 와", "thread_id": "t", "history": [],
                             "events": [], "cost_usd": 0.0}, config={"configurable": {"thread_id": "test-healthy"}})
    types = [e["type"] for e in r["events"]]
    assert "RunbookSkipped" in types and not any(c["source"] == "runbook" for c in r["collected"])
    assert "web-01" in r["final"]
