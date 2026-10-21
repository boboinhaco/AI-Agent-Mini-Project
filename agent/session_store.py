"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소 + 요약 메모리.

서버가 재시작돼도 같은 session.db 파일을 다시 열므로 대화 이력이 유지된다.
messages  : 원문 메시지 전부 (감사/재요약용으로 삭제하지 않는다)
summaries : 스레드별 누적 요약과 '몇 번째 메시지까지 요약됐는지'(covered)
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "session.db"


class SqliteSessionStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " thread_id TEXT, role TEXT, content TEXT, created TEXT)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS summaries ("
            " thread_id TEXT PRIMARY KEY, summary TEXT, covered INTEGER, updated TEXT)")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS incidents ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT, query TEXT,"
            " incident_type TEXT, systems TEXT, report TEXT, created TEXT)")
        self.conn.commit()

    # ── 원문 ─────────────────────────────────────────────────────────────
    def load_messages(self, thread_id: str, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE thread_id=? ORDER BY rowid "
            "LIMIT -1 OFFSET ?", (thread_id, offset)).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def count(self, thread_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM messages WHERE thread_id=?",
                                 (thread_id,)).fetchone()[0]

    def append(self, thread_id: str, role: str, content: str) -> None:
        self.conn.execute("INSERT INTO messages VALUES (?,?,?,?)",
                          (thread_id, role, content, datetime.now().isoformat()))
        self.conn.commit()

    # ── 요약 ─────────────────────────────────────────────────────────────
    def load_summary(self, thread_id: str) -> tuple[str, int]:
        row = self.conn.execute("SELECT summary, covered FROM summaries WHERE thread_id=?",
                                (thread_id,)).fetchone()
        return (row[0], row[1]) if row else ("", 0)

    def save_summary(self, thread_id: str, summary: str, covered: int) -> None:
        self.conn.execute(
            "INSERT INTO summaries VALUES (?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET "
            "summary=excluded.summary, covered=excluded.covered, updated=excluded.updated",
            (thread_id, summary, covered, datetime.now().isoformat()))
        self.conn.commit()

    # ── LLM에 넘길 이력 = [요약(system)] + 요약 이후 원문 ──────────────────
    def load(self, thread_id: str) -> list[dict]:
        summary, covered = self.load_summary(thread_id)
        recent = self.load_messages(thread_id, offset=covered)
        if summary:
            return [{"role": "system", "content": f"[이전 대화 요약] {summary}"}] + recent
        return recent

    def stats(self, thread_id: str) -> dict:
        summary, covered = self.load_summary(thread_id)
        history = self.load(thread_id)
        return {"total_messages": self.count(thread_id), "summarized_messages": covered,
                "history_messages": len(history),
                "history_chars": sum(len(m["content"]) for m in history),
                "summary": summary}

    # ── 인시던트 리포트 ──────────────────────────────────────────────────
    def save_incident(self, thread_id: str, query: str, incident_type: str | None,
                      systems: list[str], report: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO incidents (thread_id, query, incident_type, systems, report, created)"
            " VALUES (?,?,?,?,?,?)",
            (thread_id, query, incident_type, ",".join(systems), report,
             datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()
        return cur.lastrowid

    def list_incidents(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, thread_id, query, incident_type, systems, created FROM incidents"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "thread_id": r[1], "query": r[2], "incident_type": r[3],
                 "systems": r[4].split(",") if r[4] else [], "created": r[5]} for r in rows]

    def get_incident(self, incident_id: int) -> dict | None:
        r = self.conn.execute(
            "SELECT id, thread_id, query, incident_type, systems, report, created FROM incidents"
            " WHERE id=?", (incident_id,)).fetchone()
        if not r:
            return None
        return {"id": r[0], "thread_id": r[1], "query": r[2], "incident_type": r[3],
                "systems": r[4].split(",") if r[4] else [], "report": r[5], "created": r[6]}

    def close(self) -> None:
        self.conn.close()
