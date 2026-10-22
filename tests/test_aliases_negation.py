"""업무 용어 별칭, 부정 문맥 제외, 재계획 상한."""
from agent.nodes.approval import detect_risky_actions
from agent.state import aliased_systems, relevant_systems, target_systems


def test_business_words_map_to_systems():
    assert target_systems("결제가 안 되는 것 같아요") == ["payments-service"]
    assert target_systems("로그인이 안 돼요") == ["auth-service"]
    assert target_systems("payments-service 결제 느려요") == ["payments-service"]     # 명시 이름 우선
    assert aliased_systems("오늘 날씨 어때?") == []


def test_negated_actions_are_not_risky():
    assert detect_risky_actions("재시작은 안 돼요. 대신 로그를 확인하세요.") == []
    assert detect_risky_actions("롤백 금지 — 원인 분석까지만 진행") == []
    assert detect_risky_actions("1) 즉시 재시작 2) 롤백 검토") == ["서비스/프로세스 재시작", "배포 롤백"]


def mon(name, status, alerts):
    return {"source": "monitoring", "data": {"system_name": name, "status": status, "alerts": alerts}}


def test_relevant_systems_capped():
    col = [mon(f"s{i}-service", "degraded", ["cpu high"]) for i in range(6)]
    assert len(relevant_systems(col, "cpu-spike")) == 3


def test_plan_drops_systems_not_asked_for():
    from agent.plan_utils import normalize_plan
    raw = '{"needs_analysis": false, "tasks": [{"agent":"research","target":"payment-service"},{"agent":"research","target":"auth-service"}]}'
    targets = [t["target"] for t in normalize_plan(raw, "payment-service 에서 메모리가 부족하대")["tasks"]]
    assert targets == ["payments-service"]          # 오타는 별칭으로 교정, 덧붙인 auth-service는 제외
    raw2 = '{"needs_analysis": false, "tasks": [{"agent":"research","target":"gateway-service"}]}'
    targets = [t["target"] for t in normalize_plan(raw2, "gateway-service에서 payments-service로 가는 요청이 타임아웃")["tasks"]]
    assert set(targets) == {"gateway-service", "payments-service"}   # 빠뜨린 시스템은 보충
