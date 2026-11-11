"""Database layer: SQLite for the local demo, PostgreSQL/Supabase when DATABASE_URL is set."""
from __future__ import annotations
import json, re
import sqlite3
from datetime import datetime
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text
from .config import DATABASE_URL, ROOT

_engine = None

def is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite")

def backend_label() -> str:
    return "SQLite (local demo)" if is_sqlite() else "Supabase / PostgreSQL"

def engine():
    global _engine
    if _engine is None:
        if is_sqlite():
            (ROOT / "data").mkdir(exist_ok=True)
            _engine = create_engine(DATABASE_URL, connect_args={"timeout": 30})
        else:
            _engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    return _engine

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")

def rows(sql: str, **p) -> list[dict]:
    with engine().connect() as c:
        return [dict(r) for r in c.execute(text(sql), p).mappings()]

def df(sql: str, **p) -> pd.DataFrame:
    with engine().connect() as c:
        return pd.read_sql(text(sql), c, params=p)

def execute(sql: str, **p) -> None:
    with engine().begin() as c:
        c.execute(text(sql), p)

def insert_id(sql: str, **p) -> int:
    with engine().begin() as c:
        return int(c.execute(text(sql + " RETURNING id"), p).scalar())

def bulk_insert(conn, table: str, records: list[dict]) -> None:
    if not records:
        return
    cols = list(records[0].keys())
    conn.execute(text(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})"), records)

def init_db(seed: bool = True) -> None:
    ddl = (Path(__file__).with_name("schema.sql")).read_text()
    if not is_sqlite():
        ddl = re.sub(r"(?<![\w])id INTEGER PRIMARY KEY", "id SERIAL PRIMARY KEY", ddl)
    with engine().begin() as c:
        for stmt in ddl.split(";"):
            if stmt.strip():
                c.execute(text(stmt))
    if seed and not rows("SELECT 1 AS x FROM materials LIMIT 1"):
        from .seed import seed_demo
        seed_demo()



def reset_demo() -> None:
    if not is_sqlite():
        raise RuntimeError(
            "Reset is only allowed on the local SQLite demo database."
        )

    global _engine

    # Close idle SQLAlchemy connections
    if _engine is not None:
        _engine.dispose(close=True)
        _engine = None

    p = ROOT / "data" / "sentinel.db"
    p.parent.mkdir(parents=True, exist_ok=True)

    conn = None

    try:
        # Open SQLite directly
        conn = sqlite3.connect(str(p), timeout=30)

        # Disable foreign keys temporarily for table removal
        conn.execute("PRAGMA foreign_keys = OFF")

        objects = conn.execute("""
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
            AND name NOT LIKE 'sqlite_%'
        """).fetchall()

        # Remove views first, then tables
        for object_type, name in objects:
            if object_type == "view":
                conn.execute(f'DROP VIEW IF EXISTS "{name}"')

        for object_type, name in objects:
            if object_type == "table":
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')

        conn.commit()

    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"Could not reset database: {e}. "
            "Close other running app instances and try again."
        )

    finally:
        if conn is not None:
            conn.close()

    # Recreate tables and demo data
    init_db(seed=True)



# ───────── approvals & audit ─────────
def create_action(actor: str, action: str, target: str, payload: dict, status: str = "pending_approval", role: str = "analyst") -> int:
    return insert_id(
        "INSERT INTO action_history (actor, role, action, target, payload, status, created_at, updated_at) "
        "VALUES (:a,:r,:ac,:t,:p,:s,:n,:n)",
        a=actor, r=role, ac=action, t=target, p=json.dumps(payload, default=str), s=status, n=now())

def get_action(action_id: int) -> dict | None:
    r = rows("SELECT * FROM action_history WHERE id=:i", i=int(action_id))
    if not r:
        return None
    a = r[0]
    for k in ("payload", "result"):
        try:
            a[k] = json.loads(a[k]) if a[k] else {}
        except Exception:
            a[k] = {}
    return a

def set_action(action_id: int, **fields) -> None:
    allowed = {"status", "result", "approved_by", "approved_role", "payload"}
    sets, params = [], {"i": int(action_id), "n": now()}
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=:{k}")
            params[k] = json.dumps(v, default=str) if k in ("result", "payload") else v
    if sets:
        execute(f"UPDATE action_history SET {','.join(sets)}, updated_at=:n WHERE id=:i", **params)

def approve_action(action_id: int, user: str, role: str) -> None:
    if role not in ("approver", "admin"):
        raise PermissionError("Your role cannot approve actions. Switch to an 'approver' or 'admin' role in Settings.")
    a = get_action(action_id)
    if not a or a["status"] != "pending_approval":
        raise ValueError("Action is not awaiting approval.")
    set_action(action_id, status="approved", approved_by=user, approved_role=role)

def audit(actor: str, action: str, target: str, payload: dict, status: str, result: dict | None = None) -> int:
    i = create_action(actor, action, target, payload, status=status)
    if result is not None:
        set_action(i, result=result)
    return i

def set_integration(service: str, mode: str, status: str, detail: str) -> None:
    execute("DELETE FROM integration_status WHERE service=:s", s=service)
    execute("INSERT INTO integration_status (service, mode, status, detail, checked_at) VALUES (:s,:m,:st,:d,:n)",
            s=service, m=mode, st=status, d=detail[:500], n=now())
