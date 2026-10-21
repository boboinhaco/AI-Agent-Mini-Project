"""검증 규칙과 위험 조치 감지 — 순수 함수 단위 테스트."""
from agent.nodes.approval import detect_risky_actions
from agent.nodes.validate import run_checks

COL = [{"source": "monitoring", "data": {"system_name": "auth-service", "status": "degraded",
                                          "cpu_percent": 81.8, "memory_percent": 60.0, "alerts": ["x"]}}]
Q = "auth-service 느려요"


def test_short_draft_rejected():
    reasons = run_checks("재시작하면 됩니다.", Q, COL)
    assert any("너무 짧" in r for r in reasons)
    assert any("auth-service" in r for r in reasons)


def test_good_draft_passes_with_exact_or_rounded_value():
    assert run_checks("1) auth-service 상태=degraded, CPU 81.8% 2) 절차 3) 문서화", Q, COL) == []
    assert run_checks("1) auth-service CPU 82% 입니다 2) 절차 3) 문서화", Q, COL) == []
    assert run_checks("1) auth-service 메모리 60.0% 2) 절차 3) 문서화", Q, COL) == []   # 메모리만 인용해도 OK


def test_uncited_value_rejected():
    reasons = run_checks("1) auth-service 가 느립니다 2) 절차 3) 문서화", Q, COL)
    assert any("수치" in r for r in reasons)


def test_hallucinated_system_rejected_but_quoted_allowed():
    draft = "1) auth-service CPU 81.8% 2) billing-service도 확인 3) 끝"
    assert any("billing-service" in r for r in run_checks(draft, Q, COL))
    assert run_checks(draft, "billing-service 느려요, auth-service도", COL) == []   # 질문에 있던 이름은 허용


def test_detect_risky_actions():
    assert detect_risky_actions("OOM 직전이면 해당 프로세스를 즉시 재시작") == ["서비스/프로세스 재시작"]
    assert "배포 롤백" in detect_risky_actions("직전 배포를 롤백하고")
    assert detect_risky_actions("모니터링 수치를 확인하세요") == []
