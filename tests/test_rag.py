"""RAG — runbook 벡터 인덱스가 증상 설명으로 올바른 incident_type을 고르는지 (해싱 임베딩, 결정론)."""
import pytest

from agent.rag import HashEmbeddings, RunbookIndex, build_query_text


def mon(name, status, alerts):
    return {"source": "monitoring", "data": {"system_name": name, "status": status, "alerts": alerts}}


@pytest.mark.parametrize("query,collected,expected", [
    ("payments-service CPU가 치솟았어요", [mon("payments-service", "degraded", ["CPU 91% (threshold 80%)"])], "cpu-spike"),
    ("cache-service 메모리 사용률이 계속 올라가요", [mon("cache-service", "degraded", ["heap near limit"])], "memory-leak"),
    ("log-service 디스크 공간이 부족해요", [mon("log-service", "degraded", ["disk usage 95% on /var/log"])], "disk-full"),
    ("gateway-service 요청이 타임아웃 나요", [mon("gateway-service", "degraded", ["upstream timeout (504)"])], "network-timeout"),
    ("web-01 CPU 급증", [mon("web-01", "healthy", [])], "cpu-spike"),        # Before: db-down으로 오폴백
    ("로그인 응답이 느려요", [mon("auth-service", "degraded", ["p95 latency 2.4s", "connection pool 92% used"])], "high-latency"),
    ("DB가 죽은 것 같아요 접속이 안 돼요", [], "db-down"),
])
@pytest.mark.asyncio
async def test_classify(query, collected, expected):
    idx = RunbookIndex(HashEmbeddings()).build()
    assert idx.size == 6
    hit = await idx.classify(build_query_text(query, collected))
    assert hit["incident_type"] == expected


def test_query_text_drops_healthy_noise():
    text = build_query_text("web-01 CPU 급증", [mon("web-01", "healthy", [])])
    assert text == "web-01 CPU 급증"
