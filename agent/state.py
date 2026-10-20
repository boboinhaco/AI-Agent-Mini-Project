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
    r"응답\s*없|에러|오류|504|500|저하|알림|경보|incident|티켓|확인해|원인|분석해",
    re.IGNORECASE)


def mentioned_systems(query: str) -> list[str]:
    """쿼리에 등장하는 KNOWN_SYSTEMS를 등장 순서대로."""
    found = [(query.index(s), s) for s in KNOWN_SYSTEMS if s in query]
    return [s for _, s in sorted(found)]


def is_incident_query(query: str) -> bool:
    """코드 레벨 라우팅 판단: 알려진 시스템명이 언급되거나 장애 표현이 있으면 incident."""
    return bool(mentioned_systems(query)) or bool(INCIDENT_PATTERN.search(query))


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
    final: str
    last_prompt: str
    cost_usd: float
    events: list[dict]


def add_cost(state: AgentState, usage: dict) -> float:
    return round(state.get("cost_usd", 0.0) + usage.get("usd", 0.0), 6)
