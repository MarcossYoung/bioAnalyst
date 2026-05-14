import json
import sqlite3
import time
import uuid
from pathlib import Path

_DB_PATH = Path.home() / ".nullifier" / "runs.db"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id              TEXT PRIMARY KEY,
                raw_input       TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                created_at      REAL NOT NULL,
                completed_at    REAL,
                max_papers      INTEGER NOT NULL DEFAULT 12,
                formalized_json TEXT,
                evidence_json   TEXT,
                verdict_json    TEXT,
                analyst_json    TEXT
            );
            CREATE TABLE IF NOT EXISTS run_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL REFERENCES runs(id),
                seq          INTEGER NOT NULL,
                type         TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                ts           REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id);
        """)


def create_run(raw_input: str, max_papers: int) -> str:
    run_id = uuid.uuid4().hex[:8]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO runs (id, raw_input, status, created_at, max_papers) VALUES (?, ?, 'pending', ?, ?)",
            (run_id, raw_input, time.time(), max_papers),
        )
    return run_id


def set_status(run_id: str, status: str) -> None:
    ts = time.time() if status in ("completed", "failed", "cancelled") else None
    with _conn() as conn:
        if ts is not None:
            conn.execute(
                "UPDATE runs SET status=?, completed_at=? WHERE id=?", (status, ts, run_id)
            )
        else:
            conn.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))


def save_results(
    run_id: str,
    formalized: dict,
    evidence: dict,
    verdict: dict,
    analyst: dict | None,
) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET formalized_json=?, evidence_json=?, verdict_json=?, analyst_json=? WHERE id=?",
            (
                json.dumps(formalized, default=str),
                json.dumps(evidence, default=str),
                json.dumps(verdict, default=str),
                json.dumps(analyst, default=str) if analyst else None,
                run_id,
            ),
        )


def append_event(run_id: str, seq: int, event_type: str, payload: dict, ts: float) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO run_events (run_id, seq, type, payload_json, ts) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event_type, json.dumps(payload, default=str), ts),
        )


def get_run(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, raw_input, status, created_at, completed_at, max_papers, "
            "formalized_json, evidence_json, verdict_json, analyst_json "
            "FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "raw_input": row["raw_input"],
        "status": row["status"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "max_papers": row["max_papers"],
        "formalized": json.loads(row["formalized_json"]) if row["formalized_json"] else None,
        "evidence": json.loads(row["evidence_json"]) if row["evidence_json"] else None,
        "verdict": json.loads(row["verdict_json"]) if row["verdict_json"] else None,
        "analyst": json.loads(row["analyst_json"]) if row["analyst_json"] else None,
    }


def get_events(run_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT seq, type, payload_json, ts FROM run_events WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
    return [
        {"seq": r["seq"], "type": r["type"], "payload": json.loads(r["payload_json"]), "ts": r["ts"]}
        for r in rows
    ]


def list_runs(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, status, created_at, completed_at, max_papers, verdict_json, formalized_json "
            "FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        verdict = None
        if r["verdict_json"]:
            try:
                verdict = (json.loads(r["verdict_json"]) or {}).get("verdict")
            except Exception:
                pass
        has_completed = False
        if r["formalized_json"]:
            try:
                has_completed = bool((json.loads(r["formalized_json"]) or {}).get("completed_analysis"))
            except Exception:
                pass
        out.append({
            "id": r["id"], "status": r["status"], "created_at": r["created_at"],
            "completed_at": r["completed_at"], "max_papers": r["max_papers"],
            "verdict": verdict, "mode": "v5" if has_completed else "v4",
        })
    return out
