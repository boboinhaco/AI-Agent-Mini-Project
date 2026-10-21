"""LLM-as-judge — 측정 결과(metrics_*.json)의 답변이 수집된 수치를 정확히 인용했는지 LLM이 채점한다.

eval/measure.py의 정확도 체크는 route·조사 대상·runbook 같은 '구조' 검사다. 이 스크립트는 '내용'을 본다:
  - cites_correctly : 답변에 등장하는 시스템별 수치가 facts(모니터링 실측)와 일치하는가
  - no_invention    : facts에 없는 시스템·수치·알림을 지어내지 않았는가
  - actionable      : 권장 조치가 runbook 절차와 모순되지 않고 구체적인가
각 0/1, 총점 0~3. 채점 모델은 LLM_BACKEND(ollama=qwen3:8b / openai=gpt-4o)와 같은 축을 쓴다.
fake 백엔드에서는 규칙 기반 채점(validate.run_checks)으로 대체한다 — 결정론적 회귀용.

    .venv/bin/python eval/judge.py --metrics after/metrics_ollama.json --backend ollama --out after
"""
import argparse
import asyncio
import json
import os
import re
import sys

os.environ.setdefault("OLLAMA_FAST_TIMEOUT", "300")   # 채점은 긴 프롬프트 — 넉넉히
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

JUDGE_SYSTEM = (
    "당신은 ICT 운영 보고서 채점자다. '실측 사실'과 '답변'을 비교해 JSON 객체로만 답하라. "
    '스키마: {"cites_correctly": 0|1, "no_invention": 0|1, "actionable": 0|1, "comment": "한 문장"}. '
    "cites_correctly: 답변의 시스템별 CPU/메모리/상태 수치가 실측과 일치(반올림 허용)하면 1. "
    "no_invention: 실측에 없는 시스템명·수치·알림을 지어내지 않았으면 1. "
    "actionable: 권장 조치가 구체적이고 runbook 절차와 모순되지 않으면 1."
)


def facts_text(facts: list[dict], logs: list[dict] | None = None) -> str:
    lines = [f"- {d['system_name']}: 상태={d['status']}, CPU {d['cpu_percent']}%, "
             f"메모리 {d['memory_percent']}%, 알림 {d.get('alerts', [])}" for d in facts]
    for d in logs or []:   # 로그에서 가져온 사실(배포 버전, 중복 배치 등)도 '실측'이다
        lines.append(f"- 로그 {d['system_name']}: " + " | ".join(d.get("lines", [])))
    return "\n".join(lines) or "(없음)"


def rule_judge(reply: str, query: str, facts: list[dict]) -> dict:
    from agent.nodes.validate import run_checks
    collected = [{"source": "monitoring", "data": d} for d in facts]
    reasons = run_checks(reply, query, collected)
    return {"cites_correctly": int(not any("수치" in r or "언급하지" in r for r in reasons)),
            "no_invention": int(not any("조사하지 않은" in r for r in reasons)),
            "actionable": int("1)" in reply and "2)" in reply and "3)" in reply),
            "comment": "; ".join(reasons) or "규칙 검사 통과"}


async def llm_judge(backend: str, reply: str, query: str, facts: list[dict], logs: list[dict] | None = None) -> dict:
    if backend == "ollama":
        from agent.ollama_llm import OllamaQualityLLM
        llm = OllamaQualityLLM(os.environ.get("OLLAMA_QUALITY_MODEL", "qwen3:8b"), 0.005)
        content, _ = await llm._chat(JUDGE_SYSTEM, f"## 질문\n{query}\n\n## 실측 사실\n{facts_text(facts, logs)}\n\n## 답변\n{reply}",
                                     schema={"type": "object", "properties": {
                                         "cites_correctly": {"type": "integer"}, "no_invention": {"type": "integer"},
                                         "actionable": {"type": "integer"}, "comment": {"type": "string"}},
                                         "required": ["cites_correctly", "no_invention", "actionable", "comment"]})
    else:
        from agent.openai_llm import OpenAIQualityLLM
        llm = OpenAIQualityLLM(os.environ.get("OPENAI_QUALITY_MODEL", "gpt-4o"), 0.005)
        content, _ = await llm._chat(JUDGE_SYSTEM, f"## 질문\n{query}\n\n## 실측 사실\n{facts_text(facts, logs)}\n\n## 답변\n{reply}",
                                     json_mode=True)
    try:
        raw = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
    except Exception:
        raw = {"cites_correctly": 0, "no_invention": 0, "actionable": 0, "comment": f"파싱 실패: {content[:80]}"}
    return {k: int(bool(raw.get(k, 0))) for k in ("cites_correctly", "no_invention", "actionable")} | \
        {"comment": str(raw.get("comment", ""))[:200]}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--backend", default="fake", choices=["fake", "ollama", "openai"])
    ap.add_argument("--out", default="after")
    a = ap.parse_args()
    data = json.loads(Path(a.metrics).read_text())
    rows = []
    for r in data["rows"]:
        if r.get("route") != "incident" or r.get("error"):
            continue
        facts = r.get("facts") or []
        if not facts:
            continue
        verdict = None
        for attempt in range(2):   # 채점 모델 타임아웃 등 — 1회 재시도 후 0점 처리
            try:
                verdict = (rule_judge(r["reply"], r["query"], facts) if a.backend == "fake"
                           else await llm_judge(a.backend, r["reply"], r["query"], facts, r.get("logs")))
                break
            except Exception as e:
                verdict = {"cites_correctly": 0, "no_invention": 0, "actionable": 0,
                           "comment": f"채점 실패: {type(e).__name__}"}
        score = verdict["cites_correctly"] + verdict["no_invention"] + verdict["actionable"]
        rows.append({"id": r["id"], "query": r["query"], **verdict, "score": score})
        print(f"q{r['id']:>2} score={score}/3 cite={verdict['cites_correctly']} inv={verdict['no_invention']} "
              f"act={verdict['actionable']} | {verdict['comment'][:70]}")
    label = Path(a.metrics).stem.replace("metrics_", "")
    out = Path(a.out); out.mkdir(exist_ok=True)
    total = sum(r["score"] for r in rows); n = len(rows)
    lines = [f"# LLM-as-judge — {label} (채점: {a.backend})", "",
             f"- 채점 대상: 장애 쿼리 {n}건 · 총점 {total}/{n * 3} ({total / (n * 3) * 100:.0f}%)" if n else "- 채점 대상 없음",
             f"- cites_correctly {sum(r['cites_correctly'] for r in rows)}/{n} · no_invention {sum(r['no_invention'] for r in rows)}/{n} · actionable {sum(r['actionable'] for r in rows)}/{n}", "",
             "| # | 수치 인용 | 지어내기 없음 | 조치 구체성 | 점수 | 코멘트 |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['cites_correctly']} | {r['no_invention']} | {r['actionable']} | {r['score']}/3 | {r['comment']} |" for r in rows]
    (out / f"judge_{label}.md").write_text("\n".join(lines))
    (out / f"judge_{label}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"총점 {total}/{n * 3} → {out}/judge_{label}.md")


if __name__ == "__main__":
    asyncio.run(main())
