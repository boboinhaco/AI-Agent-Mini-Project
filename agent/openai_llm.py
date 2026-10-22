"""LLM_BACKEND=openai — 실제 OpenAI API 백엔드 (fake_llm/ollama_llm과 동일 인터페이스).

fast(기본 gpt-4o-mini)는 분류·계획·분석, quality(기본 gpt-4o)는 답변 작성·대화·요약.
실제 과금이 발생하는 원격 API다(run_agent_openai.sh 실행 전 인지할 것). 구조화 출력은
response_format=json_object(JSON 모드)로 강제하고, 필드 누락은 코드 레벨 보정으로 막는다.
프롬프트는 agent/prompts.py 한 곳에만 있다. 사전 조건: OPENAI_API_KEY(.env).
"""
import json
import os

import httpx
from langsmith import traceable

from . import prompts
from .plan_utils import normalize_plan
from .state import is_incident_query

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "120"))
ROUTE_JSON = ' JSON 객체로만 응답하라. 스키마: {"route": "chat" 또는 "incident", "needs_analysis": true 또는 false}'
PLAN_JSON = (' JSON 객체로만 응답하라. 스키마: {"needs_analysis": boolean, '
             '"tasks": [{"agent": "research"|"analysis", "target": string(선택)}]}')


class OpenAILLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"openai:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens
        self.api_key = os.environ.get("OPENAI_API_KEY", "")

    def _usage(self, tokens: int, **extra) -> dict:
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6), **extra}

    async def _chat(self, system: str, user: str, json_mode: bool = False) -> tuple[str, dict]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다 — .env 또는 환경변수로 지정하세요.")
        payload = {"model": self.model, "temperature": 0,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(OPENAI_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], self._usage(data.get("usage", {}).get("total_tokens", 0))

    async def _text(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """사용자 대면 텍스트 — 스트리밍은 아직 쓰지 않아 완성본을 한 번에 on_token으로 전달."""
        try:
            content, usage = await self._chat(system, user)
        except httpx.HTTPError as e:
            content = "[모델 응답이 제한 시간 안에 끝나지 않았습니다. 잠시 후 같은 질문을 다시 보내 주세요.]"
            usage = self._usage(0, fallback=type(e).__name__)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        if on_token is not None:
            on_token(content)
        return content, usage

    @traceable(run_type="llm", name="openai.summarize")
    async def summarize(self, previous: str, messages: list[dict]) -> tuple[str, dict]:
        try:
            return await self._chat(prompts.SUMMARIZE, prompts.summarize_user(previous, messages))
        except httpx.HTTPError as e:
            fallback = (previous + " / " if previous else "") + " / ".join(
                m["content"][:40] for m in messages if m["role"] == "user")
            return fallback, self._usage(0, fallback=type(e).__name__)

    @traceable(run_type="llm", name="openai.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        return await self._text(prompts.CHAT, prompts.chat_user(query, history), on_token=on_token)


class OpenAIFastLLM(OpenAILLM):
    """분류·계획·분석 (저비용 모델, 기본 gpt-4o-mini)."""

    @traceable(run_type="llm", name="openai_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        try:
            content, usage = await self._chat(prompts.ROUTE + ROUTE_JSON, query, json_mode=True)
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in ("chat", "incident") else "chat"
            return {"route": route, "needs_analysis": bool(raw.get("needs_analysis", False))}, usage
        except (json.JSONDecodeError, AttributeError, httpx.HTTPError) as e:
            return ({"route": "incident" if is_incident_query(query) else "chat", "needs_analysis": False},
                    self._usage(0, fallback=type(e).__name__))

    @traceable(run_type="llm", name="openai_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        try:
            content, usage = await self._chat(prompts.PLAN + PLAN_JSON,
                                              prompts.plan_user(query, procedure_prompt), json_mode=True)
        except httpx.HTTPError as e:
            content, usage = "{}", self._usage(0, fallback=type(e).__name__)
        return normalize_plan(content, query), usage

    @traceable(run_type="llm", name="openai_fast.analyze")
    async def analyze(self, collected: list[dict]) -> tuple[str, dict]:
        try:
            return await self._chat(prompts.ANALYZE, prompts.analyze_user(collected))
        except httpx.HTTPError as e:
            return prompts.fallback_analysis(collected), self._usage(0, fallback=type(e).__name__)


class OpenAIQualityLLM(OpenAILLM):
    """최종 답변 작성 (고품질 모델, 기본 gpt-4o)."""

    @traceable(run_type="llm", name="openai_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        return await self._text(prompts.WRITE, prompts.write_user(query, collected, feedback), on_token=on_token)
