"""라우팅·시스템명 헬퍼 — Before에서 오분류됐던 표현들이 incident로 잡히는지."""
import pytest

from agent.state import extract_system_name, is_incident_query, mentioned_systems


@pytest.mark.parametrize("query", [
    "payments-service 결제 응답이 느려지고 CPU가 치솟았어요",
    "cache-service 메모리 사용률이 계속 올라가고 있어요",
    "log-service 디스크 공간이 부족하다는 알림이 왔어요",
    "gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요",
    "로그인 응답이 느려요, 원인 확인해주세요",
    "billing-service 응답이 느려요",
])
def test_incident_queries_detected(query):
    assert is_incident_query(query)


@pytest.mark.parametrize("query", ["안녕, 너는 누구야?", "제 이름은 민수입니다", "제 이름이 뭐였죠?", "고마워"])
def test_chat_queries_not_incident(query):
    assert not is_incident_query(query)


def test_mentioned_systems_in_order_and_clamped():
    q = "gateway-service에서 payments-service로 가는 요청이 billing-service 때문에 타임아웃"
    assert mentioned_systems(q) == ["gateway-service", "payments-service"]   # billing은 목록 밖
    assert extract_system_name(q) == "gateway-service"
    assert extract_system_name("로그인이 느려요") == "auth-service"           # 폴백
