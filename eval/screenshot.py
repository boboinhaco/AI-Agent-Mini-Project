"""웹 채팅 화면(web_chat.html)에 질문을 보내고 결과 화면을 PNG로 저장 (Before/After 공용).

Chrome 헤드리스를 DevTools 프로토콜(CDP)로 조작한다 — playwright 등 추가 설치 불필요.

사용:
    .venv/bin/python eval/screenshot.py --url http://localhost:8600 --out before/screens \
        --query "안녕, 너는 누구야?" --name uc1_greeting
"""
import argparse
import base64
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets.sync.client as ws

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class CDP:
    def __init__(self, port: int = 9333):
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={port}",
             "--window-size=1280,900", "--hide-scrollbars", "--no-first-run",
             f"--user-data-dir={tempfile.mkdtemp(prefix='cdp-profile-')}",   # 매번 새 프로필 — 캐시된 옛 HTML 방지
             "--disk-cache-size=1", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                break
            except Exception:
                time.sleep(0.2)
        page = next(t for t in tabs if t["type"] == "page")
        self.conn = ws.connect(page["webSocketDebuggerUrl"], max_size=50_000_000)
        self.n = 0
        self.send("Page.enable"); self.send("Runtime.enable")
        self.send("Network.enable"); self.send("Network.setCacheDisabled", cacheDisabled=True)

    def send(self, method: str, **params):
        self.n += 1
        self.conn.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.conn.recv())
            if msg.get("id") == self.n:
                return msg.get("result", {})

    def eval(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def goto(self, url: str):
        self.send("Page.navigate", url=url)
        time.sleep(2.0)   # Vue CDN 로드 대기

    def screenshot(self, path: Path):
        data = self.send("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))

    def close(self):
        self.conn.close(); self.proc.terminate()


SUBMIT_JS = """
(() => {
  const input = document.querySelector('input');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, %s);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  document.querySelector('form').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
  return true;
})()
"""


APPROVE_JS = """
(() => {
  const btns = document.querySelectorAll('.approval:not(.decided) .btns button');
  if (!btns.length) return false;
  btns[%d].click(); return true;
})()
"""


def ask_and_wait(cdp: CDP, query: str, timeout: float = 600.0, decision: str | None = None):
    """decision: 'approve' | 'reject' | None — 승인 카드가 뜨면 해당 버튼을 눌러 이어간다."""
    cdp.eval(SUBMIT_JS % json.dumps(query))
    t0 = time.perf_counter()
    time.sleep(0.5)
    while time.perf_counter() - t0 < timeout:
        if decision and cdp.eval(APPROVE_JS % (0 if decision == "approve" else 1)):
            time.sleep(0.5)
            continue
        if not cdp.eval("document.querySelector('form button').disabled"):
            break
        time.sleep(0.5)
    time.sleep(0.5)
    # 프롬프트 접힘 박스는 닫힌 채로, 로그 맨 아래로 스크롤
    cdp.eval("document.querySelector('#log').scrollTop = 1e9; true")
    return round(time.perf_counter() - t0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8600")
    ap.add_argument("--out", default="before/screens")
    ap.add_argument("--name", required=True)
    ap.add_argument("--query", action="append", required=True,
                    help="여러 번 주면 같은 대화창에서 순서대로 보낸다")
    ap.add_argument("--decision", choices=["approve", "reject"], default=None,
                    help="HITL 승인 카드가 뜨면 자동으로 누를 버튼")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    cdp = CDP()
    try:
        cdp.goto(args.url)
        if args.name == "uc0_empty":
            cdp.screenshot(out / f"{args.name}.png"); print("saved", args.name); return
        for q in args.query:
            took = ask_and_wait(cdp, q, decision=args.decision)
            print(f"  '{q[:30]}...' {took}s")
        cdp.screenshot(out / f"{args.name}.png")
        print("saved", out / f"{args.name}.png")
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
