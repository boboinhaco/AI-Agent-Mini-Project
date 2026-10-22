"""그래프 공용 상태·헬퍼 — 노드 모듈들이 공통으로 쓰는 것만 모은다.

(모듈화) 기존 graph.py 한 파일에 섞여 있던 AgentState, KNOWN_SYSTEMS 로딩,
시스템명 추출, 비용 누적 헬퍼를 분리했다. 노드 모듈은 여기서만 import한다.
"""
import json
import re
from pathlib import Path
from typing import TypedDict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_known_systems() -> set[str]:
    """data/monitoring.json의 키 = 조회 가능한 시스템 목록 (단일 소스)."""
    try:
        return set(json.loads((DATA_DIR / "monitoring.json").read_text()).keys())
    except (OSError, json.JSONDecodeError):
        return {"auth-service", "web-01"}


KNOWN_SYSTEMS = load_known_systems()
DEFAULT_SYSTEM = "auth-service"

# 장애/운영 이슈로 볼 표현 — 라우팅 폴백(fake·소형 모델 형식 붕괴 시)에 공통 사용.
# Before 측정에서 fake 라우터가 "느려요/타임아웃/메모리/디스크"를 못 잡아 10개 중 9개를
# chat으로 보냈던 문제의 수정 지점.
INCIDENT_PATTERN = re.compile(
    r"장애|진단|다운|지연|급증|느려|느림|타임아웃|timeout|메모리|디스크|cpu|레이턴시|latency|"
    r"응답\s*없|에러|오류|504|500|저하|알림|알람|경보|경고|incident|티켓|원인|분석해|"
    r"먹통|죽었|접속이?\s*안|안\s*돼|안\s*되|안됨|실패|이상해|부족",
    re.IGNORECASE)
# 모델이 incident라고 했을 때만 인정하는 약한 신호 — "난 홍길동이야" 같은 잡담을 장애로 보내지 않기 위함
SOFT_INCIDENT_PATTERN = re.compile(r"서비스|시스템|서버|배포|db|api|인스턴스|노드|확인해", re.IGNORECASE)

# 증상(incident_type) ↔ 모니터링 알림 문구 키워드 — 시스템명이 없는 문의에서 전체 시스템을 훑은 뒤
# 증상과 맞는 시스템만 남길 때 쓴다 (알림 문구는 기계가 만든 영문이라 키워드 매칭이 안정적)
ALERT_KEYWORDS = {
    "cpu-spike": ["cpu", "load"],
    "memory-leak": ["memory", "heap", "oom"],
    "disk-full": ["disk", "space"],
    "high-latency": ["latency", "pool", "p95"],   # "slow"는 CPU 급증 알림에도 쓰여 제외
    "network-timeout": ["timeout", "packet", "504", "network"],
    "db-down": ["down", "database", "db "],
}


# 업무 용어 → 시스템명 (비개발 운영자는 'payments-service'를 모른다 — "결제가 안 돼요"로도 조사되게)
SYSTEM_ALIASES = {
    "payments-service": ["결제", "페이먼트", "payment", "정산"],
    "auth-service": ["로그인", "인증", "auth", "계정", "회원"],
    "cache-service": ["캐시", "cache", "세션 캐시"],
    "gateway-service": ["게이트웨이", "gateway", "api", "라우터"],
    "log-service": ["로그 서버", "로그 서비스", "로그 수집", "/var/log", "로그 저장"],
    "web-01": ["웹 서버", "홈페이지", "웹페이지", "nginx", "web"],
}
SYSTEM_LABELS = {
    "payments-service": "결제", "auth-service": "로그인/인증", "cache-service": "캐시",
    "gateway-service": "API 게이트웨이", "log-service": "로그 수집", "web-01": "웹 서버",
}


def aliased_systems(query: str) -> list[str]:
    """업무 용어로 가리킨 시스템 — 명시적 시스템명이 없을 때만 보조로 쓴다."""
    q = query.lower()
    found = []
    for name, words in SYSTEM_ALIASES.items():
        pos = [q.find(w.lower()) for w in words if w.lower() in q]
        if pos:
            found.append((min(pos), name))
    return [n for _, n in sorted(found)]


def target_systems(query: str) -> list[str]:
    """조사 대상 — 명시적 시스템명 우선, 없으면 업무 용어 별칭."""
    return mentioned_systems(query) or aliased_systems(query)


def system_label(name: str) -> str:
    return f"{name}({SYSTEM_LABELS[name]})" if name in SYSTEM_LABELS else name


def mentioned_systems(query: str) -> list[str]:
    """쿼리에 등장하는 KNOWN_SYSTEMS를 등장 순서대로."""
    found = [(query.index(s), s) for s in KNOWN_SYSTEMS if s in query]
    return [s for _, s in sorted(found)]


def is_incident_query(query: str) -> bool:
    """코드 레벨 라우팅 판단: 알려진 시스템명이 언급되거나 장애 표현이 있으면 incident."""
    return bool(mentioned_systems(query)) or bool(INCIDENT_PATTERN.search(query))


def decide_route(query: str, model_route: str) -> tuple[str, str | None]:
    """모델 판단 + 코드 교정. 반환: (route, 교정 내용 또는 None).
    - 코드가 장애 신호를 찾으면 모델이 뭐라 했든 incident
    - 모델이 incident라 해도 장애 신호도 약한 신호도 없으면 chat (소형 모델이 "난 홍길동이야"를 장애로 보낸 실측 사례)"""
    if is_incident_query(query):
        return "incident", ("chat→incident" if model_route != "incident" else None)
    if model_route == "incident" and SOFT_INCIDENT_PATTERN.search(query):
        return "incident", None
    return "chat", ("incident→chat" if model_route == "incident" else None)


MAX_SCREENED = 3   # 전체 탐색 후 남길 시스템 상한 — 많이 남기면 검증(수치 인용) 부담이 커진다


def relevant_systems(collected: list[dict], incident_type: str) -> list[str]:
    """전체 탐색 결과 중 증상과 맞는 시스템 — 알림 키워드 매칭, 없으면 저하(degraded/down) 시스템. 최대 MAX_SCREENED."""
    keywords = ALERT_KEYWORDS.get(incident_type, [])
    mon = [c["data"] for c in collected if c.get("source") == "monitoring"]
    matched = [d["system_name"] for d in mon
               if any(k in (" ".join(d.get("alerts", [])) + " " + d.get("status", "")).lower() for k in keywords)]
    if not matched:
        matched = [d["system_name"] for d in mon if d.get("status") not in ("healthy", "unknown")]
    return matched[:MAX_SCREENED]


def extract_system_name(query: str) -> str:
    """MCP Prompt(incident_analysis_prompt)에 넘길 대표 시스템 하나 — 가장 먼저 등장한 것."""
    found = mentioned_systems(query)
    return found[0] if found else DEFAULT_SYSTEM


class AgentState(TypedDict, total=False):
    query: str
    thread_id: str
    history: list[dict]
    route: str
    needs_analysis: bool
    tasks: list[dict]
    gate_removed: int
    collected: list[dict]
    replan_rounds: int
    retry_count: int
    parallel_elapsed: float
    draft: str
    validation_attempts: int
    validation_feedback: str
    approval: dict | None
    approval_rejections: int
    discovery: bool
    clarify: bool
    draft_revision: int
    approved_actions: list[str]
    rejected_actions: list[str]
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]


def add_cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage.get("usd", 0.0), 6)
