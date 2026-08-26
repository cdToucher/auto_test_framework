"""全局项目注册表：~/.atk/registry.db（SQLite，只存索引不存用例）。"""
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
CREATE TABLE IF NOT EXISTS runs_index(
  run_id TEXT, project_path TEXT, started_at TEXT,
  status TEXT, pass_n INT, fail_n INT, blocked_n INT,
  PRIMARY KEY(run_id, project_path)
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
        c.execute("DELETE FROM runs_index WHERE project_path=?", (path,))


def rebuild_index(db: Path | None = None) -> int:
    """扫描所有注册项目的 reports/runs，重建 runs_index。返回条目数。"""
    import yaml

    entries = []
    for proj in list_projects(db):
        runs_dir = Path(proj["path"]) / "reports" / "runs"
        if not runs_dir.exists():
            continue
        for d in runs_dir.iterdir():
            f = d / "run.yaml"
            if not f.is_file():
                continue
            try:
                rec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except Exception:
                continue
            scs = rec.get("scenarios") or []
            entries.append((
                rec.get("run_id", d.name), proj["path"], rec.get("created_at"),
                rec.get("status", ""), sum(1 for s in scs if s.get("passed")),
                sum(1 for s in scs if not s.get("passed")),
                sum(1 for s in scs if s.get("error_class") == "blocked"),
            ))
    with _conn(db) as c:
        c.execute("DELETE FROM runs_index")
        c.executemany(
            "INSERT OR REPLACE INTO runs_index VALUES(?,?,?,?,?,?,?)", entries)
    return len(entries)
