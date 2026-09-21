"""全局项目注册表：~/.atk/registry.db（SQLite，只存项目索引不存用例）。"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".atk" / "registry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE,
  name TEXT,
  last_seen_at TEXT
);
"""


def _conn(path: Path | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.executescript(_SCHEMA)
    return c


def upsert_project(root: Path, db: Path | None = None) -> int:
    with _conn(db) as c:
        cur = c.execute(
            "INSERT INTO projects(path,name,last_seen_at) VALUES(?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET last_seen_at=?",
            (str(root), root.name, time.strftime("%Y-%m-%dT%H:%M:%S"),
             time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        return cur.lastrowid or 0


def list_projects(db: Path | None = None) -> list[dict]:
    with _conn(db) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT * FROM projects ORDER BY last_seen_at DESC").fetchall()
        return [dict(x) for x in rows]


def remove_project(path: str, db: Path | None = None) -> None:
    with _conn(db) as c:
        c.execute("DELETE FROM projects WHERE path=?", (path,))
