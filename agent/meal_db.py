"""식사 기록(meals) 테이블 — mcp_server.py 프로세스와 LocalMenuRepo 가 같이 쓰는 모듈.

server 프로세스(session_store: messages, recommendations)와 MCP subprocess(meals)가
같은 session.db 파일을 열기 때문에 WAL + timeout 으로 열고, 문장마다 바로 commit 한다
(await 사이에 트랜잭션을 열어 두지 않는다).
"""
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = Path(os.getenv("MENU_DB_PATH") or ROOT / "session.db")


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meals ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " user_id TEXT NOT NULL, menu_name TEXT NOT NULL, eaten_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meals_user ON meals(user_id, eaten_at)")
    conn.commit()
    return conn


def insert_meal(conn: sqlite3.Connection, user_id: str, menu_name: str) -> dict:
    eaten_at = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO meals (user_id, menu_name, eaten_at) VALUES (?,?,?)",
        (user_id, menu_name, eaten_at),
    )
    conn.commit()
    return {"id": cur.lastrowid, "menu_name": menu_name, "eaten_at": eaten_at}


def select_meals(conn: sqlite3.Connection, user_id: str, days: int = 3) -> list[dict]:
    """최근 days 일 안의 기록, 최신순."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT id, menu_name, eaten_at FROM meals"
        " WHERE user_id=? AND eaten_at>=? ORDER BY eaten_at DESC, id DESC",
        (user_id, since),
    ).fetchall()
    return [{"id": i, "menu_name": n, "eaten_at": t} for i, n, t in rows]
