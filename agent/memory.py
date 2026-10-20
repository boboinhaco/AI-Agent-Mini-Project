"""대화 요약 메모리 — 대화가 길어지면 과거 턴을 요약해 압축 저장한다.

Before: session_store.load()가 스레드의 모든 메시지를 매 요청 LLM 프롬프트에 그대로 실었다
        (턴이 늘수록 토큰·지연 선형 증가, chat 프롬프트는 마지막 10개만 잘라 그 이전은 잊음).
After : 요약되지 않은 메시지가 THRESHOLD개를 넘으면 최근 KEEP_RECENT개만 남기고 나머지를
        fast_llm.summarize()로 요약 → summaries 테이블에 누적 저장. load()는
        [요약 1건(system)] + 최근 메시지만 돌려준다.
"""
import os

from langsmith import traceable

THRESHOLD = int(os.environ.get("MEMORY_SUMMARY_THRESHOLD", "10"))   # 요약 트리거(미요약 메시지 수)
KEEP_RECENT = int(os.environ.get("MEMORY_KEEP_RECENT", "4"))        # 원문으로 남길 최근 메시지 수


@traceable(name="compact_history")
async def compact_history(deps, thread_id: str) -> dict | None:
    """필요 시 과거 턴을 요약해 저장. 요약이 일어났으면 {"summarized": n, ...} 반환."""
    store = deps.session_store
    previous, covered = store.load_summary(thread_id)
    pending = store.load_messages(thread_id, offset=covered)
    if len(pending) <= THRESHOLD:
        return None
    to_summarize = pending[:-KEEP_RECENT] if KEEP_RECENT else pending
    # 요약은 10메시지마다 1회뿐이라 비용 부담이 작다 — 소형(fast) 모델은 이름 같은 핵심 사실을
    # 뭉개는 것이 실측됐으므로(qwen3:0.6b) 품질 모델이 맡는다.
    summary, usage = await deps.quality_llm.summarize(previous, to_summarize)
    store.save_summary(thread_id, summary, covered + len(to_summarize))
    return {"summarized": len(to_summarize), "summary_chars": len(summary),
            "cost_usd": usage.get("usd", 0.0)}
