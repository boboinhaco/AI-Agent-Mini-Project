"""LLM_BACKEND=ollama — 로컬 Ollama 백엔드 (fake_llm/openai_llm과 동일 인터페이스).

fast(기본 qwen3:0.6b)는 분류·계획·분석, quality(기본 qwen3:8b)는 답변 작성·대화·요약 전담.
프롬프트는 agent/prompts.py 한 곳에만 있고, 여기는 "Ollama를 어떻게 호출하나"(JSON 스키마 강제,
스트리밍, 타임아웃 폴백)만 담당한다. 비용(cost_usd)은 가상 단가 × 실측 토큰 수다.
"""
import json
import os

import httpx
from langsmith import traceable

from . import prompts
from .plan_utils import normalize_plan
from .state import is_incident_query

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
FAST_TIMEOUT = float(os.environ.get("OLLAMA_FAST_TIMEOUT", "90"))      # 분류·계획·분석·요약
STREAM_TIMEOUT = float(os.environ.get("OLLAMA_STREAM_TIMEOUT", "300"))  # 답변 작성·대화(스트리밍)

ROUTE_SCHEMA = {"type": "object",
                "properties": {"route": {"type": "string", "enum": ["chat", "incident"]},
                               "needs_analysis": {"type": "boolean"}},
                "required": ["route", "needs_analysis"]}
PLAN_SCHEMA = {"type": "object",
               "properties": {"needs_analysis": {"type": "boolean"},
                              "tasks": {"type": "array", "items": {
                                  "type": "object",
                                  "properties": {"agent": {"type": "string", "enum": ["research", "analysis"]},
                                                 "target": {"type": "string"}},
                                  "required": ["agent"]}}},
               "required": ["needs_analysis", "tasks"]}


class OllamaLLM:
    def __init__(self, model: str, usd_per_1k_tokens: float):
        self.name = f"ollama:{model}"
        self.model = model
        self.usd_per_1k = usd_per_1k_tokens

    def _usage(self, tokens: int, **extra) -> dict:
        return {"model": self.name, "tokens": tokens,
                "usd": round(tokens / 1000 * self.usd_per_1k, 6), **extra}

    async def _chat(self, system: str, user: str, schema: dict | None = None) -> tuple[str, dict]:
        """비스트리밍 호출 — JSON 스키마 출력(route/plan/analyze/summarize)용."""
        payload = {"model": self.model, "stream": False, "options": {"temperature": 0.0},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if schema is not None:
            payload["format"] = schema
        async with httpx.AsyncClient(timeout=FAST_TIMEOUT) as client:   # 비동기 — 이벤트 루프를 막지 않는다
            resp = await client.post(OLLAMA_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        tokens = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0)
        return data["message"]["content"], self._usage(tokens)

    async def _chat_stream(self, system: str, user: str, on_token=None) -> tuple[str, dict]:
        """사용자 대면 텍스트(답변·대화) 전용 — 토큰을 on_token으로 즉시 흘려보낸다.
        타임아웃이면 예외로 스트림을 죽이지 않고, 받은 조각 + 안내문으로 마무리한다."""
        payload = {"model": self.model, "stream": True, "options": {"temperature": 0.0},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        chunks: list[str] = []
        usage = self._usage(0)
        try:
            async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
                async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        piece = data.get("message", {}).get("content", "")
                        if piece:
                            chunks.append(piece)
                            if on_token is not None:
                                on_token(piece)
                        if data.get("done"):
                            usage = self._usage((data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0))
        except httpx.HTTPError as e:
            usage["fallback"] = type(e).__name__
            note = ("\n\n[모델 응답이 제한 시간 안에 끝나지 않아 여기까지만 전달합니다. "
                    "잠시 후 같은 질문을 다시 보내 주세요.]")
            chunks.append(note)
            if on_token is not None:
                on_token(note)
        usage["prompt"] = f"[SYSTEM]\n{system}\n\n[USER]\n{user}"
        return "".join(chunks), usage

    # ── 두 모델이 공유하는 능력 ──
    @traceable(run_type="llm", name="ollama.summarize")
    async def summarize(self, previous: str, messages: list[dict]) -> tuple[str, dict]:
        try:
            return await self._chat(prompts.SUMMARIZE, prompts.summarize_user(previous, messages))
        except httpx.HTTPError as e:   # 요약 실패는 치명적이지 않다 — 추출 요약으로 대체
            fallback = (previous + " / " if previous else "") + " / ".join(
                m["content"][:40] for m in messages if m["role"] == "user")
            return fallback, self._usage(0, fallback=type(e).__name__)

    @traceable(run_type="llm", name="ollama.chat")
    async def chat(self, query: str, history: list[dict], on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(prompts.CHAT, prompts.chat_user(query, history), on_token=on_token)


class OllamaFastLLM(OllamaLLM):
    """분류·계획·분석 (소형 모델). 형식 붕괴·타임아웃은 코드 레벨 폴백으로 복구한다."""

    @traceable(run_type="llm", name="ollama_fast.route")
    async def route(self, query: str) -> tuple[dict, dict]:
        try:
            content, usage = await self._chat(prompts.ROUTE, query, schema=ROUTE_SCHEMA)
            raw = json.loads(content)
            route = raw.get("route") if raw.get("route") in ("chat", "incident") else "chat"
            return {"route": route, "needs_analysis": bool(raw.get("needs_analysis", False))}, usage
        except (json.JSONDecodeError, AttributeError, httpx.HTTPError) as e:
            return ({"route": "incident" if is_incident_query(query) else "chat", "needs_analysis": False},
                    self._usage(0, fallback=type(e).__name__))

    @traceable(run_type="llm", name="ollama_fast.plan")
    async def plan(self, query: str, procedure_prompt: str) -> tuple[dict, dict]:
        try:
            content, usage = await self._chat(prompts.PLAN, prompts.plan_user(query, procedure_prompt),
                                              schema=PLAN_SCHEMA)
        except httpx.HTTPError as e:   # 소형 모델 무응답 → 코드 레벨 계획으로 복구 (Before 500 오류 지점)
            content, usage = "{}", self._usage(0, fallback=type(e).__name__)
        return normalize_plan(content, query), usage

    @traceable(run_type="llm", name="ollama_fast.analyze")
    async def analyze(self, collected: list[dict]) -> tuple[str, dict]:
        try:
            return await self._chat(prompts.ANALYZE, prompts.analyze_user(collected))
        except httpx.HTTPError as e:
            return prompts.fallback_analysis(collected), self._usage(0, fallback=type(e).__name__)


class OllamaQualityLLM(OllamaLLM):
    """최종 답변 작성 (상대적으로 큰 모델) — 대화·요약도 base 클래스 메서드로 담당."""

    @traceable(run_type="llm", name="ollama_quality.write")
    async def write(self, query: str, collected: list[dict], attempt: int,
                    feedback: str | None, on_token=None) -> tuple[str, dict]:
        return await self._chat_stream(prompts.WRITE, prompts.write_user(query, collected, feedback),
                                       on_token=on_token)
