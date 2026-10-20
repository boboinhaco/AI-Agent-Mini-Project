"""요약 메모리 Before/After 확인 — 한 스레드에 긴 대화를 넣고 LLM에 실리는 이력 크기를 본다.

    .venv/bin/python eval/memory_check.py --base http://127.0.0.1:8101 --out after --label fake
"""
import argparse
import json
import uuid
from pathlib import Path

import httpx

TURNS = ["제 이름은 민수입니다", "안녕, 너는 누구야?", "오늘 날씨 어때?", "점심 메뉴 추천해줘",
         "나는 서울에 살아", "취미는 등산이야", "주말에 뭐 하면 좋을까?",
         "고마워", "제 이름이 뭐였죠?"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", default="after")
    a = ap.parse_args()
    thread = f"memory-{a.label}-{uuid.uuid4().hex[:6]}"   # 실행마다 새 스레드
    rows = []
    with httpx.Client(timeout=600) as c:
        for i, t in enumerate(TURNS, 1):
            r = c.post(f"{a.base}/chat", json={"message": t, "thread_id": thread}).json()
            st = c.get(f"{a.base}/session/{thread}").json() if c.get(f"{a.base}/session/{thread}").status_code == 200 else {}
            rows.append({"turn": i, "message": t, "reply": r.get("reply"), "memory": r.get("memory"),
                         **{k: st.get(k) for k in ("total_messages", "summarized_messages",
                                                   "history_messages", "history_chars")}})
            print(f"turn {i:>2} total={st.get('total_messages')} summarized={st.get('summarized_messages')} "
                  f"history_msgs={st.get('history_messages')} history_chars={st.get('history_chars')} | {r.get('reply','')[:40]}")
        final = c.get(f"{a.base}/session/{thread}").json()
    out = Path(a.out); out.mkdir(exist_ok=True)
    (out / f"memory_{a.label}.json").write_text(json.dumps({"rows": rows, "final": final}, ensure_ascii=False, indent=2))
    print("summary:", final.get("summary"))
    print("last reply (이름 기억?):", rows[-1]["reply"])


if __name__ == "__main__":
    main()
