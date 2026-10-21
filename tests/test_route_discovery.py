"""양방향 라우팅 교정과 시스템명 없는 문의의 전체 탐색·선별."""
import pytest

from agent.state import decide_route, relevant_systems
from conftest import run_config


@pytest.mark.parametrize("query,model,expected", [
    ("난 홍길동이야", "incident", "chat"),                 # 모델 오탐 → chat
    ("지금 내가 질문 몇 개 했어", "incident", "chat"),
    ("넌 누구야 뭘 할 수 있어", "chat", "chat"),
    ("디스크 공간이 부족하다고 알람이 왔어", "chat", "incident"),   # 코드 신호 → incident
    ("서비스가 좀 이상한 것 같은데 봐줘", "incident", "incident"),   # 약한 신호 + 모델 incident
])
def test_decide_route(query, model, expected):
    assert decide_route(query, model)[0] == expected


def mon(name, status, alerts):
    return {"source": "monitoring", "data": {"system_name": name, "status": status, "alerts": alerts}}


def test_relevant_systems_by_alert_keywords():
    col = [mon("log-service", "degraded", ["disk usage 95% on /var/log"]),
           mon("auth-service", "degraded", ["p95 latency 2.4s"]), mon("web-01", "healthy", [])]
    assert relevant_systems(col, "disk-full") == ["log-service"]
    assert relevant_systems(col, "high-latency") == ["auth-service"]
    assert relevant_systems(col, "cpu-spike") == ["log-service", "auth-service"]   # 키워드 없으면 저하 시스템


@pytest.mark.asyncio
async def test_discovery_screens_to_matching_system(graph):
    r = await graph.ainvoke({"query": "디스크 공간이 부족하다고 알람이 왔어", "thread_id": "t",
                             "history": [], "events": [], "cost_usd": 0.0}, config=run_config("disc"))
    t = [e["type"] for e in r["events"]]
    assert "SystemDiscovery" in t and "SystemsScreened" in t
    screened = next(e for e in r["events"] if e["type"] == "SystemsScreened")
    assert screened["kept"] == ["log-service"] and screened["incident_type"] == "disk-full"
    facts = {c["data"]["system_name"] for c in r["collected"] if c["source"] == "monitoring"}
    assert facts == {"log-service"}
    assert "log-service" in r["final"] and "auth-service" not in r["final"]


@pytest.mark.asyncio
async def test_name_intro_goes_to_chat_and_is_remembered(graph, deps):
    r = await graph.ainvoke({"query": "난 홍길동이야", "thread_id": "t", "history": [], "events": [],
                             "cost_usd": 0.0}, config=run_config("name"))
    assert r["route"] == "chat" and "홍길동" in r["final"]
    reply, _ = await deps.fast_llm.chat("제 이름이 뭐였죠?", [{"role": "user", "content": "난 홍길동이야"}])
    assert "홍길동" in reply
    reply, _ = await deps.fast_llm.chat("넌 누구야 뭘 할 수 있어", [])
    assert "인시던트" in reply
