"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소.

서버가 재시작돼도(새 프로세스, 새 인스턴스) 같은 session.db 파일을 다시 열므로
대화 이력과 최근 추천이 유지된다. in-memory dict였다면 재시작 즉시 소실됐을 상태다.

테이블: messages(sample 그대로, thread_id 에 user_id), recommendations(최근 추천 제외용).
식사 기록(meals)은 같은 파일이지만 agent/meal_db.py 가 소유한다(MCP 서버 프로세스와 공용).
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv("MENU_DB_PATH") or Path(__file__).parent.parent / "session.db")


class SqliteSessionStore:
    def __init__(self, db_path: Path = DB_PATH):
        # MCP subprocess 도 같은 파일에 meals 를 쓰므로 WAL + timeout, 문장마다 commit
        self.conn = sqlite3.connect(str(db_path), timeout=5, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " thread_id TEXT, role TEXT, content TEXT, created TEXT)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS recommendations ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id TEXT NOT NULL, menu_name TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recommendations_user"
            " ON recommendations(user_id, id)"
        )
        self.conn.commit()

    def load(self, thread_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE thread_id=? ORDER BY rowid",
            (thread_id,),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def append(self, thread_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?)",
            (thread_id, role, content, datetime.now().isoformat()),
        )
        self.conn.commit()

    def recent_recommendations(self, user_id: str, limit: int = 6) -> list[str]:
        """id 기준 최근 limit 개를 오래된 → 최신 순으로."""
        rows = self.conn.execute(
            "SELECT menu_name FROM recommendations WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [r[0] for r in reversed(rows)]

    def save_recommendations(self, user_id: str, names: list[str]) -> None:
        if not names:
            return
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.executemany(
            "INSERT INTO recommendations (user_id, menu_name, created_at) VALUES (?,?,?)",
            [(user_id, n, now) for n in names],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
