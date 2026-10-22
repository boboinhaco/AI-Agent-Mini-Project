"""LLM 백엔드 공용 프롬프트와 입력 렌더링 — ollama/openai/fake 어댑터가 함께 쓴다.

프롬프트가 어댑터마다 복사돼 있으면 한쪽만 고쳐지는 사고가 난다(정리 6차). 어댑터는 "모델을 어떻게 호출하나"만
담당하고, "무엇을 시키나"는 여기 한 곳에만 둔다.
"""
from .state import KNOWN_SYSTEMS, SYSTEM_LABELS

SYSTEM_LIST = ", ".join(f"{n}({SYSTEM_LABELS.get(n, '')})" for n in sorted(KNOWN_SYSTEMS))

ROUTE = ("당신은 ICT 운영 지원 라우터다. 사용자 요청을 분류하라. "
         "장애·성능 저하·지연·진단·모니터링·티켓 등 운영 이슈면 route=incident, "
         "인사·잡담·일반 질문이면 route=chat. "
         "needs_analysis는 패턴 비교/통계 분석이 꼭 필요할 때만 true.")

PLAN = ("당신은 ICT 운영 Supervisor의 planning 노드다. 표준 진단 절차에 따라 "
        "실행 계획(tasks)을 JSON으로 세워라. "
        "사용자 요청이 가리키는 시스템마다 agent=research task를 하나씩 세워라 "
        f"(알려진 시스템: {', '.join(sorted(KNOWN_SYSTEMS))}). 요청에 없는 시스템을 임의로 추가하지 마라. "
        "'결제'는 payments-service, '로그인'은 auth-service처럼 업무 용어는 해당 시스템으로 해석하라. "
        "패턴 분석이 꼭 필요하다고 판단되면 agent=analysis task를 추가해도 된다.")

ANALYZE = ("당신은 ICT 운영 패턴 분석가다. 여러 시스템의 모니터링 결과를 비교해 "
           "공통 원인이 의심되는 패턴이 있는지 한두 문장으로 짧게 답하라. "
           "패턴이 없으면 '특이 패턴 없음'이라고만 답하라.")

CHAT = ("당신은 '운영 인시던트 대응 에이전트'다. 운영 중 장애·성능 저하·알림 같은 문제를 말하면 "
        "모니터링 지표·최근 로그·대응 매뉴얼을 조회해 원인과 해결 방안을 안내하는 것이 역할이다. "
        f"지금 확인할 수 있는 시스템: {SYSTEM_LIST}. "
        "자기소개나 '무엇을 할 수 있냐/뭘 물어보면 되냐'는 질문에는 이 역할과 위 시스템 목록을 알려주고, "
        "'예: payments-service 결제가 느려요 / 디스크 부족 알람이 왔어요'처럼 질문 예시를 2개 들어라. "
        "문제를 말했는데 어느 시스템인지 모호하면 위 목록 중 어느 것인지 되물어라. "
        "그 외 가벼운 대화는 한국어 한두 문장으로 간결히 답하라. 대화 이력(요약 포함)에서 사용자가 "
        "밝힌 이름이 있으면 정확히 기억해 답하라. '내가 질문 몇 개 했어' 같은 대화 이력에 관한 "
        "질문은 아래 대화 이력의 user 메시지를 직접 세어 숫자로 답하라. 이전 장애 보고를 반복하지 마라.")

SUMMARIZE = ("당신은 대화 기록 요약기다. 이전 요약과 새 대화를 합쳐 아래 형식으로만 한국어로 답하라. "
             "사용자가 밝힌 이름은 반드시 그대로 보존하라.\n"
             "사용자 이름: <이름 또는 미상>\n"
             "주요 사실: <쉼표로 구분한 핵심 사실 3개 이내>\n"
             "진행 중인 요청: <있으면 한 줄, 없으면 없음>")

WRITE = ("당신은 ICT 운영 보고 작성자다. 수집된 사실만 사용해 한국어로 간결한 대응 보고를 작성하라. "
         "반드시 아래 다섯 항목을 '1) 2) 3) 4) 5)' 번호로 쓰고 하위 목록은 만들지 마라(항목당 1~2문장). "
         "1) 상태: 어느 시스템이 어떤 상태인지, 핵심 수치와 기준치(예: 메모리 89% — 기준 80%) "
         "2) 원인 추정: 로그·알림에서 보이는 단서 "
         "3) 지금 할 일(운영자): 당장 확인·조치할 것 "
         "4) 담당팀에 요청: 개발/인프라팀에 넘길 것 "
         "5) 근거: 인용한 수치·로그 한 줄. "
         "영문 용어(p95, heap, OOM, TTL 등)는 괄호로 쉬운 한글 설명을 붙이고, 수치는 수집된 값을 그대로 인용하라. "
         "시스템 이름(예: auth-service)을 반드시 1)에 적어라.")


def plan_user(query: str, procedure_prompt: str) -> str:
    return f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"


def monitoring_facts(collected: list[dict]) -> list[dict]:
    return [c["data"] for c in collected if c.get("source") == "monitoring"]


def analyze_user(collected: list[dict]) -> str:
    mon = monitoring_facts(collected)
    return "\n".join(f"- {d['system_name']}: 상태={d['status']}, CPU {d['cpu_percent']}%, "
                     f"알림 {d['alerts']}" for d in mon) or "(모니터링 데이터 없음)"


def fallback_analysis(collected: list[dict]) -> str:
    """모델 없이 하는 비교 분석 — fake 백엔드와 실모델 타임아웃 폴백이 공유."""
    mon = monitoring_facts(collected)
    degraded = [d["system_name"] for d in mon if d.get("status") != "healthy"]
    if len(degraded) >= 2:
        return f"{', '.join(degraded)} 복수 시스템이 동시에 저하됨 — 공유 의존성 등 공통 원인이 의심됨"
    if degraded:
        return f"{degraded[0]}만 단독 저하 — 연관 시스템은 정상, 국소 이슈로 추정"
    return "모니터링된 시스템 모두 healthy — 패턴상 이상 징후 없음"


RECENT_TURNS = 12


def chat_user(query: str, history: list[dict]) -> str:
    """요약(system 메시지)은 길이와 무관하게 항상 넣고, 원문은 최근 RECENT_TURNS개만.
    (실측 버그: history[-10:]로 자르면 맨 앞의 요약이 떨어져 나가 이름을 잊었다)"""
    summary = [m for m in history if m.get("role") == "system"]
    recent = [m for m in history if m.get("role") != "system"][-RECENT_TURNS:]
    rendered = "\n".join(f"{m['role']}: {m['content']}" for m in summary + recent)
    return f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n## 사용자의 새 메시지\n{query}"


def summarize_user(previous: str, messages: list[dict]) -> str:
    rendered = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    return f"## 이전 요약\n{previous or '(없음)'}\n\n## 새 대화\n{rendered}"


def write_user(query: str, collected: list[dict], feedback: str | None) -> str:
    facts = []
    for c in collected:
        if c["source"] == "monitoring":
            d = c["data"]
            facts.append(f"- 모니터링 {d['system_name']}: 상태={d['status']}, "
                         f"CPU {d['cpu_percent']}%, 메모리 {d['memory_percent']}%, "
                         f"알림 {len(d['alerts'])}건 {d['alerts']}")
        elif c["source"] == "logs":
            d = c["data"]
            facts.append(f"- 최근 로그 {d['system_name']}:\n  " + "\n  ".join(d.get("lines", [])))
        elif c["source"] == "runbook":
            facts.append(f"- runbook 발췌:\n{c['data'].strip()}")
        elif c["source"] == "analysis":
            facts.append(f"- 패턴 분석: {c['data'].strip()}")
    user = f"## 사용자 요청\n{query}\n\n## 수집된 사실\n" + "\n".join(facts)
    if feedback:
        user += f"\n\n## 이전 초안에 대한 검증 반려 사유 (반드시 반영)\n{feedback}"
    return user
