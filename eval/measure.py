"""회귀 쿼리 10개를 /chat에 보내 수치를 기록하는 측정 스크립트 (Before/After 공용).

사용:
    .venv/bin/python eval/measure.py --base http://127.0.0.1:8100 --label ollama --out before
결과:
    <out>/metrics_<label>.json   쿼리별 원본(응답·이벤트·metadata·지연시간)
    <out>/metrics_<label>.csv    표 형태 요약
    <out>/summary_<label>.md     사람이 읽는 요약 + 정확도 체크
"""
import argparse
import csv
import json
import time
import uuid
from pathlib import Path

import httpx

# (쿼리, 기대값) — doc/regression_test_queries.md 기준
QUERIES = [
    {"id": 1, "q": "payments-service 결제 응답이 느려지고 CPU가 치솟았어요, 원인 확인해주세요",
     "route": "incident", "systems": {"payments-service"}, "runbook": "cpu-spike"},
    {"id": 2, "q": "cache-service 메모리 사용률이 계속 올라가고 있어요",
     "route": "incident", "systems": {"cache-service"}, "runbook": "memory-leak"},
    {"id": 3, "q": "log-service 디스크 공간이 부족하다는 알림이 왔어요",
     "route": "incident", "systems": {"log-service"}, "runbook": "disk-full"},
    {"id": 4, "q": "gateway-service에서 payments-service로 가는 요청이 타임아웃 나고 있어요",
     "route": "incident", "systems": {"gateway-service", "payments-service"}, "runbook": None},
    {"id": 5, "q": "web-01 CPU 급증, auth-service 커넥션 풀도 같이 확인해줘",
     "route": "incident", "systems": {"web-01", "auth-service"}, "runbook": None},
    {"id": 6, "q": "payments-service랑 cache-service 둘 다 저하됐는데 공통 원인이 있는지 분석해줘",
     "route": "incident", "systems": {"payments-service", "cache-service"}, "runbook": None,
     "analysis": True},
    {"id": 7, "q": "로그인 응답이 느려요, 원인 확인해주세요",
     "route": "incident", "systems": {"auth-service"}, "runbook": None},
    {"id": 8, "q": "billing-service 응답이 느려요",
     "route": "incident", "systems": {"auth-service"}, "runbook": None, "forbid": {"billing-service"}},
    {"id": 9, "q": "안녕, 너는 누구야?", "route": "chat"},
    {"id": 10, "q": "제 이름은 민수입니다", "followup": "제 이름이 뭐였죠?", "route": "chat",
     "reply_contains": "민수"},
]


def post_chat(client: httpx.Client, base: str, message: str, thread_id: str) -> tuple[dict, float]:
    t0 = time.perf_counter()
    r = client.post(f"{base}/chat", json={"message": message, "thread_id": thread_id})
    elapsed = round(time.perf_counter() - t0, 2)
    r.raise_for_status()
    return r.json(), elapsed


def check(spec: dict, data: dict) -> dict:
    """기대값 대비 통과 여부 — 코드 변경 전후 '정확도'를 같은 잣대로 비교하기 위함."""
    meta, events, reply = data["metadata"], data.get("events", []), data["reply"]
    results = {}
    results["route_ok"] = meta.get("route") == spec["route"]
    if spec["route"] == "incident":
        investigated = {e["target"] for e in events
                        if e.get("type") == "TaskCompleted" and e.get("source") == "monitoring"}
        runbooks = {e["target"] for e in events
                    if e.get("type") == "TaskCompleted" and e.get("source") == "runbook"}
        results["systems_ok"] = spec["systems"].issubset(investigated)
        if spec.get("forbid"):
            # 목록에 없는 시스템을 '조사 대상'으로 삼지 않았는지 (질문 인용은 허용)
            results["no_fake_system"] = not (spec["forbid"] & investigated)
        if spec.get("runbook"):
            results["runbook_ok"] = spec["runbook"] in runbooks
        if spec.get("analysis"):
            results["analysis_ran"] = any(e.get("type") == "AnalysisCompleted" for e in events)
        results["format_ok"] = "1)" in reply and len(reply) >= 30
        # 수집된 수치가 답변에 인용됐는지 (monitoring 결과의 cpu 값 중 하나라도 등장)
        results["investigated"] = sorted(investigated)
        results["runbooks"] = sorted(runbooks)
    if spec.get("reply_contains"):
        results["memory_ok"] = spec["reply_contains"] in reply
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--label", required=True, help="예: fake / ollama / after-ollama")
    ap.add_argument("--out", default="before")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    run_tag = uuid.uuid4().hex[:6]
    rows = []
    with httpx.Client(timeout=600.0) as client:
        health = client.get(f"{args.base}/health").json()
        print("health:", health)
        for spec in QUERIES:
            thread = f"{args.label}-{run_tag}-q{spec['id']}"
            try:
                data, elapsed = post_chat(client, args.base, spec["q"], thread)
                if spec.get("followup"):
                    data, elapsed2 = post_chat(client, args.base, spec["followup"], thread)
                    elapsed += elapsed2
                error = None
            except Exception as e:   # 서버 500 등 — 실패도 기록하고 다음 쿼리로 진행
                error = f"{type(e).__name__}: {e}"
                data = {"reply": f"[ERROR] {error}", "events": [], "metadata": {}}
                elapsed = -1
                print(f"q{spec['id']:>2} ERROR {error}")
            meta = data["metadata"]
            checks = check(spec, data) if not error else {"server_error": False}
            bool_checks = {k: v for k, v in checks.items() if isinstance(v, bool)}
            row = {
                "id": spec["id"], "query": spec.get("followup") and f"{spec['q']} → {spec['followup']}" or spec["q"],
                "latency_s": elapsed,
                "route": meta.get("route"), "task_count": meta.get("task_count"),
                "gate_removed": meta.get("gate_removed"), "replan_rounds": meta.get("replan_rounds"),
                "retry_count": meta.get("retry_count"), "parallel_elapsed": meta.get("parallel_elapsed"),
                "validation_attempts": meta.get("validation_attempts"), "cost_usd": meta.get("cost_usd"),
                "reply_len": len(data["reply"]),
                "checks_passed": sum(bool_checks.values()), "checks_total": len(bool_checks),
                "investigated": ",".join(checks.get("investigated", [])),
                "runbooks": ",".join(checks.get("runbooks", [])),
                "langsmith_run_id": meta.get("langsmith_run_id"),
            }
            rows.append({**row, "checks": checks, "reply": data["reply"], "error": error,
                         "events": data.get("events", []), "metadata": meta})
            if error:
                continue
            print(f"q{spec['id']:>2} {elapsed:>6.1f}s route={row['route']:<8} tasks={row['task_count']} "
                  f"val={row['validation_attempts']} cost={row['cost_usd']} "
                  f"checks={row['checks_passed']}/{row['checks_total']}")

    (out / f"metrics_{args.label}.json").write_text(
        json.dumps({"health": health, "rows": rows}, ensure_ascii=False, indent=2))
    csv_cols = ["id", "query", "latency_s", "route", "task_count", "gate_removed", "replan_rounds",
                "retry_count", "parallel_elapsed", "validation_attempts", "cost_usd", "reply_len",
                "checks_passed", "checks_total", "investigated", "runbooks"]
    with (out / f"metrics_{args.label}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r[c] for c in csv_cols})

    n = len(rows)
    tot_lat = sum(r["latency_s"] for r in rows)
    tot_cost = sum(r["cost_usd"] or 0 for r in rows)
    passed = sum(r["checks_passed"] for r in rows)
    total = sum(r["checks_total"] for r in rows)
    lines = [f"# 측정 요약 — {args.label}", "",
             f"- 백엔드: {health.get('llm_backend')} (fast={health.get('fast_llm')}, quality={health.get('quality_llm')})",
             f"- supervisor_version: {health.get('supervisor_version')}",
             f"- 쿼리 수: {n}", f"- 총 지연시간: {tot_lat:.1f}s (평균 {tot_lat/n:.1f}s)",
             f"- 총 cost_usd(가상 단가): {tot_cost:.6f}",
             f"- 정확도 체크 통과: {passed}/{total} ({passed/total*100:.0f}%)",
             f"- 검증 재작성 발생 쿼리: {sum(1 for r in rows if (r['validation_attempts'] or 0) > 1)}건",
             f"- 재시도(retry) 총합: {sum(r['retry_count'] or 0 for r in rows)}", "",
             "| # | 지연(s) | route | tasks | 검증시도 | cost | 조사 시스템 | runbook | 체크 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        failed = [k for k, v in r["checks"].items() if v is False]
        lines.append(f"| {r['id']} | {r['latency_s']} | {r['route']} | {r['task_count']} | "
                     f"{r['validation_attempts']} | {r['cost_usd']} | {r['investigated']} | {r['runbooks']} | "
                     f"{r['checks_passed']}/{r['checks_total']}{' ✗' + ','.join(failed) if failed else ''} |")
    lines += ["", "## 답변 전문", ""]
    for r in rows:
        lines += [f"### q{r['id']}: {r['query']}", "", "```", r["reply"], "```", ""]
    (out / f"summary_{args.label}.md").write_text("\n".join(lines))
    print(f"\n저장: {out}/metrics_{args.label}.{{json,csv}}, summary_{args.label}.md")
    print(f"정확도 {passed}/{total}, 총 지연 {tot_lat:.1f}s, 총 cost {tot_cost:.6f}")


if __name__ == "__main__":
    main()
