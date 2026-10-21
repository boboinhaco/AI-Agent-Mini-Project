"""LLM_BACKEND=openai — 실제 OpenAI API 백엔드 (fake_llm/ollama_llm과 동일 인터페이스).

Wave 10a 구도를 실제 상용 API로: fast(기본 gpt-4o-mini)는 분류·계획·단순 대화,
quality(기본 gpt-4o)는 최종 답변 작성 전담. ollama_llm.py와 마찬가지로 실모델이라
결정론은 아니다 — gate(W6)·replan(W7)·validator(W8)가 연출이 아니라 실제 방어선으로
동작한다. Ollama와 달리 실제 과금이 발생하는 원격 API이므로, 이 백엔드로 실행하면
OpenAI 계정에 실제 비용이 청구된다(run_openai.sh 실행 전 반드시 인지할 것).

구조화 출력은 OpenAI Chat Completions의 `response_format={"type": "json_object"}`
(JSON 모드)로 강제한다 — Ollama의 `format=<JSON Schema>`처럼 스키마 자체를 강제하는
기능은 아니라서, 시스템 프롬프트에 원하는 필드를 명시하고 ollama_llm.py와 동일하게
코드 레벨 방어(파싱 실패·필드 누락 시 보정)를 그대로 둔다.

비용(cost_usd)은 OpenAI 응답의 `usage.total_tokens`(실측) × 가상 단가다 — 실제 청구
금액과는 별개로, W10a의 모델 분리 효과를 fake/ollama와 같은 잣대로 비교하기 위함이다.
(OpenAI는 입력/출력 토큰 단가가 실제로는 다르지만, 세 백엔드의 인터페이스를
`usd_per_1k_tokens` 하나로 통일해두기 위해 여기서도 단일 블렌디드 단가로 단순화한다.)

사전 조건: 유효한 `OPENAI_API_KEY` 환경변수(.env).
"""
import json
import os

import httpx
from langsmith import traceable

from .plan_utils import normalize_plan
from .state import KNOWN_SYSTEMS, SYSTEM_LABELS, is_incident_query

SYSTEM_LIST = ", ".join(f"{n}({SYSTEM_LABELS.get(n, '')})" for n in sorted(KNOWN_SYSTEMS))

OPENAI_URL = "https://api.openai.com/v1/chat/completions"




ROUTE_INSTRUCTIONS = (
    'JSON 객체로만 응답하라. 스키마: '
    '{"route": "chat" 또는 "incident", "needs_analysis": true 또는 false}'
)

PLAN_INSTRUCTIONS = (
    'JSON 객체로만 응답하라. 스키마: {"needs_analysis": boolean, '
    '"tasks": [{"agent": "research"|"analysis", "target": string(선택)}]}'
)


class OpenAILLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    async def _chat(self, system: str, user: str, json_mode: bool = False) -> tuple[str, dict]:
        if not self.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요."
            )
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(OPENAI_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        usage = {"model": self.name, "tokens": tokens,
                 "usd": round(tokens / 1000 * self.usd_per_1k, 6)}
        return content, usage

    @traceable(run_type="llm", name="openai.summarize")
    async def summarize(self, previous: str, messages: list[dict]) -> tuple[str, dict]:
        system = ("당신은 대화 기록 요약기다. 이전 요약과 새 대화를 합쳐 아래 형식으로만 한국어로 답하라. "
                  "사용자가 밝힌 이름은 반드시 그대로 보존하라.\n"
                  "사용자 이름: <이름 또는 미상>\n"
                  "주요 사실: <쉼표로 구분한 핵심 사실 3개 이내>\n"
                  "진행 중인 요청: <있으면 한 줄, 없으면 없음>")
        rendered = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        user = f"## 이전 요약\n{previous or '(없음)'}\n\n## 새 대화\n{rendered}"
        try:
            return await self._chat(system, user)
        except httpx.HTTPError as e:   # 요약 실패는 치명적이지 않다 — 추출 요약으로 대체
            fallback = (previous + " / " if previous else "") + " / ".join(
                m["content"][:40] for m in messages if m["role"] == "user")
            return fallback, {"model": self.name, "tokens": 0, "usd": 0.0, "fallback": type(e).__name__}

    @traceable(run_type="llm", name="openai.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        """ollama_llm.py와 인터페이스(on_token 콜백)만 맞춘다 — OpenAI Chat Completions를
        아직 stream=True로 호출하지 않아 토큰 단위 중계는 하지 않고 완성된 응답을 한 번에
        전달한다."""
        system = ("당신은 '운영 인시던트 대응 에이전트'다. 운영 중 장애·성능 저하·알림 같은 문제를 말하면 "
                  "모니터링 지표·최근 로그·대응 매뉴얼을 조회해 원인과 해결 방안을 안내하는 것이 역할이다. "
                  f"지금 확인할 수 있는 시스템: {SYSTEM_LIST}. "
                  "자기소개나 '무엇을 할 수 있냐/뭘 물어보면 되냐'는 질문에는 이 역할과 위 시스템 목록을 알려주고, "
                  "'예: payments-service 결제가 느려요 / 디스크 부족 알람이 왔어요'처럼 질문 예시를 2개 들어라. "
                  "문제를 말했는데 어느 시스템인지 모호하면 위 목록 중 어느 것인지 되물어라. "
                  "그 외 가벼운 대화는 한국어 한두 문장으로 간결히 답하라. 대화 이력(요약 포함)에서 사용자가 "
                  "밝힌 이름이 있으면 정확히 기억해 답하라. '내가 질문 몇 개 했어' 같은 대화 이력에 관한 "
                  "질문은 아래 대화 이력의 user 메시지를 직접 세어 숫자로 답하라. 이전 장애 보고를 반복하지 마라.")
        rendered = "\n".join(f"{m['role']}: {m['content']}" for m in history[-10:])
        user = (f"## 지금까지의 대화\n{rendered or '(없음)'}\n\n"
                f"## 사용자의 새 메시지\n{query}")
        content, usage = await self._chat(system, user)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage


class OpenAIFastLLM(OpenAILLM):
    """분류·계획·단순 대화 전담 (저비용 모델, 기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        system = ("당신은 ICT 운영 지원 라우터다. 사용자 요청을 분류하라. "
                  "장애·성능 저하·지연·진단·모니터링·티켓 등 운영 이슈면 route=incident, "
                  "인사·잡담·일반 질문이면 route=chat. "
                  "needs_analysis는 패턴 비교/통계 분석이 꼭 필요할 때만 true. "
                  + ROUTE_INSTRUCTIONS)
        try:
            content, usage = await self._chat(system, query, json_mode=True)
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in ("chat", "incident") else None
            result = {"route": route or "chat",
                      "needs_analysis": bool(raw.get("needs_analysis", False))}
        except (json.JSONDecodeError, AttributeError, httpx.HTTPError) as e:
            # JSON 모드는 "유효한 JSON"만 보장할 뿐 스키마까지 강제하진 않으므로,
            # 필드 누락·오탐·타임아웃 시 코드 레벨 fallback을 둔다(ollama_llm.py와 동일).
            usage = {"model": self.name, "tokens": 0, "usd": 0.0, "fallback": type(e).__name__}
            result = {"route": "incident" if is_incident_query(query) else "chat",
                      "needs_analysis": False}
        return result, usage

    @traceable(run_type="llm", name="openai_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        system = ("당신은 ICT 운영 Supervisor의 planning 노드다. 표준 진단 절차에 따라 "
                  "실행 계획(tasks)을 JSON으로 세워라. "
                  "사용자 요청에 등장한 시스템마다 agent=research task를 하나씩 세워라 "
                  f"(알려진 시스템: {', '.join(sorted(KNOWN_SYSTEMS))} — 특정 시스템이 "
                  "언급되지 않았으면 auth-service를 기본으로 조사하라). "
                  "패턴 분석이 꼭 필요하다고 판단되면 agent=analysis task를 추가해도 된다. "
                  + PLAN_INSTRUCTIONS)
        user = f"## 표준 절차\n{procedure_prompt}\n\n## 사용자 요청\n{query}"
        try:
            content, usage = await self._chat(system, user, json_mode=True)
        except httpx.HTTPError as e:
            content = "{}"
            usage = {"model": self.name, "tokens": 0, "usd": 0.0, "fallback": type(e).__name__}

        return normalize_plan(content, query), usage

    @traceable(run_type="llm", name="openai_fast.analyze")
    async def analyze(self, collected: list[dict]) -> tuple[str, dict]:
        system = ("당신은 ICT 운영 패턴 분석가다. 여러 시스템의 모니터링 결과를 비교해 "
                  "공통 원인이 의심되는 패턴이 있는지 한두 문장으로 짧게 답하라. "
                  "패턴이 없으면 '특이 패턴 없음'이라고만 답하라.")
        mon = [c["data"] for c in collected if c["source"] == "monitoring"]
        facts = "\n".join(
            f"- {d['system_name']}: 상태={d['status']}, CPU {d['cpu_percent']}%, "
            f"알림 {d['alerts']}" for d in mon) or "(모니터링 데이터 없음)"
        try:
            return await self._chat(system, facts)
        except httpx.HTTPError as e:   # 타임아웃 등 — 코드 레벨 비교로 대체 (fake_llm.analyze와 동일 규칙)
            degraded = [d["system_name"] for d in mon if d.get("status") != "healthy"]
            if len(degraded) >= 2:
                text = f"{', '.join(degraded)} 복수 시스템이 동시에 저하됨 — 공유 의존성 등 공통 원인이 의심됨"
            elif degraded:
                text = f"{degraded[0]}만 단독 저하 — 연관 시스템은 정상, 국소 이슈로 추정"
            else:
                text = "특이 패턴 없음"
            return text, {"model": self.name, "tokens": 0, "usd": 0.0, "fallback": type(e).__name__}




class OpenAIQualityLLM(OpenAILLM):
    """최종 답변 작성 전담 (고품질 모델, 기본 gpt-4o)."""

    @traceable(run_type="llm", name="openai_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
              feedback: str | None, on_token=None) -> tuple[str, dict]:
        """ollama_llm.py와 인터페이스(on_token 콜백)만 맞춘다 — OpenAI Chat Completions를
        아직 stream=True로 호출하지 않아 토큰 단위 중계는 하지 않고 완성된 응답을 한 번에
        전달한다."""
        system = ("당신은 ICT 운영 보고 작성자다. 수집된 사실만 사용해 한국어로 간결한 대응 보고를 작성하라. "
                  "반드시 아래 다섯 항목을 '1) 2) 3) 4) 5)' 번호로 쓰고 하위 목록은 만들지 마라(항목당 1~2문장). "
                  "1) 상태: 어느 시스템이 어떤 상태인지, 핵심 수치와 기준치(예: 메모리 89% — 기준 80%) "
                  "2) 원인 추정: 로그·알림에서 보이는 단서 "
                  "3) 지금 할 일(운영자): 당장 확인·조치할 것 "
                  "4) 담당팀에 요청: 개발/인프라팀에 넘길 것 "
                  "5) 근거: 인용한 수치·로그 한 줄. "
                  "영문 용어(p95, heap, OOM, TTL 등)는 괄호로 쉬운 한글 설명을 붙이고, 수치는 수집된 값을 그대로 인용하라. "
                  "시스템 이름(예: auth-service)을 반드시 1)에 적어라.")
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
        content, usage = await self._chat(system, user)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage
